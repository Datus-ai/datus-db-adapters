# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import csv
import io
import json
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Self

import pymongo
from bson import json_util
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument
from pydantic import ConfigDict, Field, SecretStr, field_validator
from pymongo.errors import ConfigurationError, OperationFailure, PyMongoError
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError

from datus_db_core import BaseSqlConnector, ConnectionConfig, DatusDbException, ErrorCode, ExecuteSQLResult

from .mongo_sql import MongoSql

_READ_STAGES = frozenset(
    {"$match", "$project", "$sort", "$skip", "$limit", "$group", "$count", "$set", "$addFields", "$unset"}
)
_SERVER_CODE = frozenset({"$where", "$function", "$accumulator"})
_MAX_RESULT_ROWS = 10_000
_MAX_SQL_RESULT_BYTES = 32 * 1024 * 1024
_MAX_SAMPLES = 100


class MongoDBConfig(ConnectionConfig):
    model_config = ConfigDict(hide_input_in_errors=True)

    uri: SecretStr
    database: str = Field(min_length=1)
    schema_file: Path | None = None
    timeout_seconds: int = Field(default=30, gt=0)
    read_only: Literal[True] = True

    @field_validator("uri")
    @classmethod
    def validate_scheme(cls, uri: SecretStr) -> SecretStr:
        if not uri.get_secret_value().startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("Expected a mongodb:// or mongodb+srv:// URI")
        return uri


def _document(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected a MongoDB document")
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json_util.loads(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Invalid MongoDB Extended JSON") from None
    return value


def _check_expressions(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, expression in value.items():
            if key in _SERVER_CODE:
                raise ValueError("Server-side JavaScript is unavailable")
            _check_expressions(expression)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_expressions(item)


def _check_limit(limit: int, maximum: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"Expected a row limit from 1 through {maximum}")


def _record_fields(document: dict[str, Any], fields: dict[str, set[str]], prefix: str = "") -> None:
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else key
        fields[path].add(type(value).__name__ if value is not None else "null")
        if isinstance(value, dict):
            _record_fields(value, fields, path)


def _empty_aggregate_row(query: exp.Select) -> list[int | None] | None:
    """Preserve SQL's single global aggregate row when MongoDB groups no documents."""
    if query.args.get("group") or query.find(exp.AggFunc) is None:
        return None
    if query.args.get("having"):
        raise ValueError("MongoSQL global aggregates with HAVING are not supported")
    limit, offset = query.args.get("limit"), query.args.get("offset")
    if (isinstance(limit, exp.Limit) and limit.expression.this == "0") or (
        isinstance(offset, exp.Offset) and offset.expression.this != "0"
    ):
        return None
    projections = [column.unalias() for column in query.expressions]
    if not all(isinstance(column, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)) for column in projections):
        raise ValueError("Select COUNT, SUM, AVG, MIN or MAX directly in an ungrouped MongoSQL aggregate")
    return [0 if isinstance(column, exp.Count) else None for column in projections]


def _guard_nullable_sum(expression: exp.Expression) -> exp.Expression:
    if not isinstance(expression, exp.Sum):
        return expression
    # MongoDB's $sum yields zero when every input is null. SQL yields null.
    count = exp.Count(this=expression.this.copy())
    empty = exp.EQ(this=count, expression=exp.Literal.number(0))
    return exp.Case(ifs=[exp.If(this=empty, true=exp.Null())], default=expression.copy())


class MongoDBConnector(BaseSqlConnector):
    """Read MongoSQL and native documents from one configured database."""

    def __init__(self, config: MongoDBConfig | dict[str, Any]):
        config = config if isinstance(config, MongoDBConfig) else MongoDBConfig.model_validate(config)
        super().__init__(config, "mongodb")
        self._default_database = config.database
        self._sql_catalog: MongoSql | None = None
        self._sql_lock = Lock()
        try:
            self._client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
                self.config.uri.get_secret_value(), serverSelectionTimeoutMS=self.config.timeout_seconds * 1000
            )
            self._database = self._client[self.config.database]
        except (ConfigurationError, ValueError):
            raise DatusDbException(ErrorCode.COMMON_CONFIG_ERROR, "Invalid MongoDB connection settings") from None

    def close(self) -> None:
        with self._sql_lock:
            self._sql_catalog = None
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _operation(self, name: str) -> Iterator[None]:
        try:
            with pymongo.timeout(self.config.timeout_seconds):
                yield
        except PyMongoError as exc:
            if isinstance(exc, OperationFailure) and exc.code == 18:
                code = ErrorCode.DB_AUTHENTICATION_FAILED
            elif isinstance(exc, OperationFailure) and exc.code == 13:
                code = ErrorCode.DB_PERMISSION_DENIED
            elif exc.timeout:
                code = ErrorCode.DB_EXECUTION_TIMEOUT
            else:
                code = ErrorCode.DB_EXECUTION_ERROR
            raise DatusDbException(code, f"MongoDB {name} failed") from None

    def test_connection(self) -> bool:
        with self._operation("ping"):
            return self._database.command("ping").get("ok") == 1

    def list_collections(self) -> list[str]:
        return self._list_collection_names({})

    def _list_collection_names(self, filter: dict[str, Any]) -> list[str]:
        with self._operation("list collections"):
            names = self._database.list_collection_names(filter=filter)
        return sorted(name for name in names if not name.startswith("system."))

    def _check_context(self, catalog_name: str, database_name: str, schema_name: str) -> None:
        if catalog_name or schema_name or (database_name and database_name != self.config.database):
            raise DatusDbException(ErrorCode.DB_PERMISSION_DENIED, "MongoDB uses its configured database only")

    def _apply_live_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> None:
        self._check_context(catalog_name, database_name, schema_name)

    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> list[str]:
        self._check_context(catalog_name, "", "")
        return [self.config.database]

    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> list[str]:
        self._check_context(catalog_name, database_name, "")
        return []

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> list[str]:
        self._check_context(catalog_name, database_name, schema_name)
        return self._list_collection_names({"type": {"$ne": "view"}})

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> list[str]:
        self._check_context(catalog_name, database_name, schema_name)
        return self._list_collection_names({"type": "view"})

    def _relation_context(self, name: str) -> dict[str, str]:
        return {"catalog_name": "", "database_name": self.config.database, "schema_name": "", "table_name": name}

    def _get_definitions(self, names: Sequence[str]) -> list[dict[str, str]]:
        definitions = []
        for name in names:
            metadata = self.describe_collection(name)
            definition = {
                "name": name,
                "type": metadata["type"],
                "declared_schema": metadata["declared_schema"],
                "sample_fields": metadata["sample_fields"],
            }
            definitions.append(
                {**self._relation_context(name), "definition": json_util.dumps(definition, ensure_ascii=False)}
            )
        return definitions

    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: list[str] | None = None,
    ) -> list[dict[str, str]]:
        names = self.get_tables(catalog_name, database_name, schema_name)
        return self._get_definitions([name for name in names if tables is None or name in tables])

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> list[dict[str, str]]:
        return self._get_definitions(self.get_views(catalog_name, database_name, schema_name))

    def get_sample_rows(
        self,
        tables: list[str] | None = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: Literal["table", "view", "mv", "full"] = "table",
    ) -> list[dict[str, Any]]:
        self._check_context(catalog_name, database_name, schema_name)
        if isinstance(top_n, bool) or not isinstance(top_n, int) or not 0 <= top_n <= _MAX_SAMPLES:
            raise ValueError(f"Expected a sample size from 0 through {_MAX_SAMPLES}")
        if top_n == 0:
            return []
        names = tables
        if names is None:
            names = []
            if table_type in {"full", "table"}:
                names.extend(self.get_tables())
            if table_type in {"full", "view"}:
                names.extend(self.get_views())
        samples = []
        for name in names:
            rows = self.find(name, limit=top_n)
            if rows:
                fields = sorted({field for row in rows for field in row})
                output = io.StringIO(newline="")
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(fields)
                for row in rows:
                    writer.writerow(
                        value
                        if value is None or type(value) in (str, int, float, bool)
                        else json_util.dumps(value, ensure_ascii=False)
                        for value in (row.get(field) for field in fields)
                    )
                samples.append({**self._relation_context(name), "sample_rows": output.getvalue()})
        return samples

    def full_name(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> str:
        self._check_context(catalog_name, database_name, schema_name)
        return self.quote_identifier(table_name)

    def get_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = "", table_name: str = ""
    ) -> list[dict[str, Any]]:
        self._check_context(catalog_name, database_name, schema_name)
        metadata = self.describe_collection(table_name)
        declared = metadata["declared_schema"] or {}
        required = set(declared.get("required", ()))
        fields = dict(metadata["sample_fields"])
        fields.update({name: field.get("bsonType", "any") for name, field in declared.get("properties", {}).items()})
        return [
            {
                "cid": index,
                "name": name,
                "type": "|".join(types) if isinstance(types, list) else types,
                "nullable": name not in required or "null" in types,
                "pk": name == "_id",
            }
            for index, (name, types) in enumerate(fields.items())
        ]

    def _schema_file_text(self) -> str:
        if self.config.schema_file is None:
            raise ValueError("MongoSQL requires a BSON schema_file for the configured MongoDB database")
        try:
            return self.config.schema_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError("Could not read MongoDB schema_file") from None

    def _mongo_sql(self) -> MongoSql:
        with self._sql_lock:
            if self._sql_catalog is None:
                schema = self._schema_file_text()
                catalog = _document(_document(json.loads(schema)).get(self.config.database))
                self._sql_catalog = MongoSql(self.config.database, schema, catalog)
            return self._sql_catalog

    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        try:
            self._check_context(catalog_name, database_name, schema_name)
            statements = [statement for statement in parse(sql, read="postgres") if statement is not None]
            if len(statements) != 1 or not isinstance(statements[0], exp.Select):
                raise ValueError("MongoDB accepts one read-only SELECT statement")
            query = statements[0]
            tables = list(query.find_all(exp.Table))
            if (
                len(tables) != 1
                or not isinstance(tables[0].this, exp.Identifier)
                or tables[0].catalog
                or (tables[0].db and tables[0].db != self.config.database)
                or query.find(exp.CTE, exp.Subquery, exp.Join, exp.Into, exp.Lock)
            ):
                raise ValueError("MongoSQL SELECT must read a single collection in the configured database")
            if result_format not in {"csv", "arrow", "pandas", "list"}:
                raise ValueError(f"Unsupported SQL result format: {result_format}")
            empty_row = _empty_aggregate_row(query)
            if query.find(exp.Sum):
                query = query.transform(_guard_nullable_sum)
                mongo_sql = query.sql(dialect="postgres")
            else:
                mongo_sql = sql
            pipeline, columns = self._mongo_sql().translate(mongo_sql, query, tables[0])
            database = self._database.with_options(
                codec_options=CodecOptions(document_class=RawBSONDocument, tz_aware=True)
            )
            rows = []
            result_bytes = 0
            with self._operation("SQL query"):
                with database[tables[0].name].aggregate(pipeline, allowDiskUse=False) as cursor:
                    for row in cursor:
                        result_bytes += len(row.raw)
                        if len(rows) == _MAX_RESULT_ROWS or result_bytes > _MAX_SQL_RESULT_BYTES:
                            raise ValueError("MongoDB result limit exceeded; use a query limit or narrower filters")
                        rows.append([column.read(row) for column in columns])
            if not rows and empty_row is not None:
                rows.append(empty_row)
            names = [column.name for column in columns]
            if result_format in {"arrow", "pandas"}:
                import pyarrow as pa

                arrays = [
                    pa.array((row[index] for row in rows), type=pa.type_for_alias(column.kind))
                    for index, column in enumerate(columns)
                ]
                result = pa.Table.from_arrays(arrays, names=names)
                if result_format == "pandas":
                    result = result.to_pandas()
            elif result_format == "csv":
                output = io.StringIO(newline="")
                writer = csv.writer(output, lineterminator="\n")
                writer.writerow(names)
                writer.writerows(rows)
                result = output.getvalue()
            else:
                result = [dict(zip(names, row, strict=True)) for row in rows]
            return ExecuteSQLResult(
                success=True, sql_query=sql, sql_return=result, row_count=len(rows), result_format=result_format
            )
        except (DatusDbException, SqlglotError, ValueError) as exc:
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql, result_format=result_format)

    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="csv")

    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="pandas")

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="arrow")

    def execute_queries(self, queries: list[str]) -> list[Any]:
        results = []
        for sql in queries:
            result = self.execute_query(sql, result_format="list")
            if not result.success:
                raise DatusDbException(ErrorCode.DB_EXECUTION_ERROR, result.error)
            results.append(result.sql_return)
        return results

    @staticmethod
    def _reject_write(sql: str, **context: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=False, error="MongoDB adapter is read-only", sql_query=sql)

    execute_insert = _reject_write
    execute_update = _reject_write
    execute_delete = _reject_write
    execute_ddl = _reject_write

    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        return self._reject_write(sql_query)

    def describe_collection(self, collection: str, *, sample_size: int = 20) -> dict[str, Any]:
        _check_limit(sample_size, _MAX_SAMPLES)
        with self._operation("describe collection"):
            with self._database.list_collections(filter={"name": collection}) as cursor:
                entry = next(cursor, None)
        if entry is None:
            raise DatusDbException(ErrorCode.DB_TABLE_NOT_EXISTS, "MongoDB collection does not exist")

        declared = entry.get("options", {}).get("validator", {}).get("$jsonSchema")
        if self.config.schema_file is not None:
            try:
                catalog = _document(json.loads(self._schema_file_text()))
                collections = _document(catalog.get(self.config.database, {}))
                if collection in collections:
                    declared = _document(collections[collection])
            except ValueError:
                raise DatusDbException(ErrorCode.COMMON_CONFIG_ERROR, "Invalid MongoDB collection schema") from None

        samples = self.find(collection, limit=sample_size)
        fields: dict[str, set[str]] = defaultdict(set)
        for doc in samples:
            _record_fields(doc, fields)
        return {
            "name": collection,
            "type": entry["type"],
            "declared_schema": declared,
            "sample_fields": {name: sorted(types) for name, types in sorted(fields.items())},
            "samples": samples,
        }

    def find(
        self,
        collection: str,
        filter: dict[str, Any] | str | None = None,
        *,
        projection: dict[str, Any] | str | None = None,
        sort: Sequence[tuple[str, int]] = (),
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        _check_limit(limit, _MAX_RESULT_ROWS)
        predicate = {} if filter is None else _document(_decode(filter))
        fields = None if projection is None else _document(_decode(projection))
        _check_expressions(predicate)
        _check_expressions(fields)
        with self._operation("find"):
            with self._database[collection].find(predicate, fields, sort=list(sort) or None, limit=limit) as cursor:
                return list(cursor)

    def aggregate(
        self, collection: str, pipeline: Sequence[dict[str, Any]] | str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        _check_limit(limit, _MAX_RESULT_ROWS)
        stages = _decode(pipeline)
        if not isinstance(stages, (list, tuple)):
            raise ValueError("Expected an array of aggregation stages")
        for item in stages:
            stage = _document(item)
            if len(stage) != 1 or next(iter(stage)) not in _READ_STAGES:
                raise ValueError("Unsupported MongoDB aggregation stage")
            _check_expressions(stage)
        with self._operation("aggregate"):
            with self._database[collection].aggregate([*stages, {"$limit": limit}]) as cursor:
                return list(cursor)
