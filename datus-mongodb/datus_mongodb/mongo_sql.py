"""Translate read-only SQL and normalize scalar BSON query results."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import DecimalException
from math import isfinite
from typing import Any, Literal

from bson import ObjectId, decode, json_util
from bson.decimal128 import Decimal128
from sqlglot import exp

from ._mongosql import MongoSqlCatalog

_COMPARISONS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)


def _decimal_literal(expression: exp.Expression) -> exp.Expression:
    literal = expression.unnest()
    sign = ""
    if isinstance(literal, exp.Neg):
        sign, literal = "-", literal.this.unnest()
    if not isinstance(literal, exp.Literal) or not literal.is_number or literal.is_int:
        return expression
    text = sign + literal.this
    try:
        decimal = Decimal128(text)
    except (DecimalException, ValueError):
        raise ValueError("Numeric literal cannot be represented exactly as MongoDB Decimal128") from None
    # MongoSQL would interpret a plain numeric string as a Double inside CAST.
    return exp.cast(exp.Literal.string(json_util.dumps(decimal)), "DECIMAL")


class MongoSql:
    def __init__(self, database: str, schema_json: str, collections: dict[str, Any]):
        self._catalog = MongoSqlCatalog(database, schema_json)
        self._collections = collections

    def translate(
        self, sql: str, query: exp.Select, collection: exp.Table
    ) -> tuple[list[dict[str, Any]], list["ResultColumn"]]:
        normalized = self._normalize_decimal_predicates(query, collection)
        if normalized is not query:
            sql = normalized.sql(dialect="postgres")
            query = normalized
        if query.args.get("distinct"):
            query = self._normalize_distinct(query, collection)
            sql = query.sql(dialect="postgres")
        plan = decode(self._catalog.translate(sql, collection.name))
        columns = [ResultColumn.from_schema(field["name"], field["schema"]) for field in plan["columns"]]
        return plan["pipeline"], columns

    def _normalize_decimal_predicates(self, query: exp.Select, collection: exp.Table) -> exp.Select:
        where = query.args.get("where")
        if where is None:
            return query
        schema = self._collections.get(collection.name, {})

        def is_decimal_column(expression: exp.Expression) -> bool:
            column = expression.unnest()
            if not isinstance(column, exp.Column):
                return False
            path = [part.name for part in column.parts]
            if len(path) > 2 and path[0] == collection.db:
                path.pop(0)
            if len(path) > 1 and path[0] in {collection.alias_or_name, collection.name}:
                path.pop(0)
            field = schema
            for name in path:
                field = field.get("properties", {}).get(name, {})
            return _bson_types(field) == {"decimal"}

        changed = False

        def normalize(predicate: exp.Expression) -> exp.Expression:
            nonlocal changed
            if isinstance(predicate, _COMPARISONS):
                keys = (
                    ("expression",)
                    if is_decimal_column(predicate.this)
                    else ("this",)
                    if is_decimal_column(predicate.expression)
                    else ()
                )
            elif isinstance(predicate, exp.Between) and is_decimal_column(predicate.this):
                keys = ("low", "high")
            elif isinstance(predicate, exp.In) and is_decimal_column(predicate.this):
                keys = ("expressions",)
            else:
                return predicate
            for key in keys:
                values = predicate.args.get(key)
                if values is None:
                    continue
                if isinstance(values, list):
                    rewritten = [_decimal_literal(value) for value in values]
                else:
                    rewritten = _decimal_literal(values)
                if rewritten != values:
                    predicate.set(key, rewritten)
                    changed = True
            return predicate

        where = where.transform(normalize)
        if not changed:
            return query
        normalized = query.copy()
        normalized.set("where", where)
        return normalized

    def _normalize_distinct(self, query: exp.Select, collection: exp.Table) -> exp.Select:
        projections = []
        for index, projection in enumerate(query.expressions, start=1):
            if projection.is_star:
                fields = self._collections.get(collection.name, {}).get("properties", {})
                if not fields:
                    raise ValueError("MongoSQL DISTINCT * requires declared collection fields")
                columns = [exp.column(name, table=collection.alias_or_name, quoted=True) for name in sorted(fields)]
            else:
                columns = [projection]
            for column in columns:
                # A BSON document with an absent key is distinct from one with a null key.
                # Make the SQL result explicit before grouping, ordering or limiting.
                expression = exp.Coalesce(this=column.unalias().copy(), expressions=[exp.Null()])
                alias = column.args.get("alias")
                if alias is None:
                    field = column.unalias()
                    alias = field.this if isinstance(field, exp.Column) else exp.to_identifier(f"_{index}")
                projections.append(exp.Alias(this=expression, alias=alias.copy()))
        return query.select(*projections, append=False)


ColumnKind = Literal["int64", "float64", "bool", "string"]


def _bson_types(schema: dict[str, Any]) -> set[str]:
    types = schema.get("bsonType", [])
    result = {types} if isinstance(types, str) else set(types)
    for variant in (*schema.get("anyOf", []), *schema.get("oneOf", [])):
        result.update(_bson_types(variant))
    return result - {"null", "undefined"}


@dataclass(frozen=True)
class ResultColumn:
    name: str
    kind: ColumnKind

    @classmethod
    def from_schema(cls, name: str, schema: dict[str, Any]) -> "ResultColumn":
        types = _bson_types(schema)
        numeric = {"int", "long", "double"}
        if types <= {"string", "objectId", "date", "decimal"} or (
            "decimal" in types and types <= numeric | {"decimal"}
        ):
            return cls(name, "string")
        if types <= {"int", "long"}:
            return cls(name, "int64")
        if types <= numeric:
            return cls(name, "float64")
        if types == {"bool"}:
            return cls(name, "bool")
        raise ValueError("MongoSQL result has an unsupported BSON type")

    def read(self, document: Any) -> str | int | float | bool | None:
        value = document.get(self.name)
        if value is None:
            return None
        if self.kind == "int64" and isinstance(value, int) and not isinstance(value, bool):
            return value
        if self.kind == "bool" and isinstance(value, bool):
            return value
        if self.kind == "float64":
            if isinstance(value, float) and isfinite(value):
                return value
            if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 1 << 53:
                return float(value)
        if self.kind == "string":
            if isinstance(value, (str, ObjectId, Decimal128)):
                return str(value)
            if isinstance(value, datetime):
                return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float) and isfinite(value):
                return str(value)
        raise ValueError("MongoSQL result value does not match its BSON schema")
