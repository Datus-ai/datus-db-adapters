# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""PostgreSQL relational backend with namespace scoping."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import Table, inspect
from sqlalchemy.exc import SQLAlchemyError

from datus.storage.backends.vector.interfaces import And, Condition, FilterExpr, Op
from datus.storage.backends.relational.interfaces import RelationalTable, TableSchema
from datus.storage.backends.relational.sqlalchemy_backend import SQLAlchemyBackend, SQLAlchemyTable
from datus.utils.exceptions import DatusException, ErrorCode
from datus.utils.loggings import get_logger

logger = get_logger(__name__)


class NamespaceRelationalTable(RelationalTable):
    def __init__(self, table: RelationalTable, namespace: str, has_namespace: bool):
        self._table = table
        self._namespace = namespace
        self._has_namespace = has_namespace

    @property
    def name(self) -> str:
        return self._table.name

    def insert(self, row: Mapping[str, Any]) -> int:
        row = self._inject_namespace(row)
        return self._table.insert(row)

    def insert_many(self, rows: Sequence[Mapping[str, Any]]) -> int:
        rows = [self._inject_namespace(row) for row in rows]
        return self._table.insert_many(rows)

    def upsert(self, row: Mapping[str, Any], conflict_columns: Sequence[str]) -> int:
        row = self._inject_namespace(row)
        conflict_columns = self._namespace_conflict_columns(conflict_columns)
        return self._table.upsert(row, conflict_columns)

    def update(self, where: Optional[FilterExpr], values: Mapping[str, Any]) -> int:
        return self._table.update(self._merge_where(where), values)

    def delete(self, where: Optional[FilterExpr]) -> int:
        return self._table.delete(self._merge_where(where))

    def select(
        self,
        columns: Optional[Sequence[str]] = None,
        where: Optional[FilterExpr] = None,
        order_by: Optional[Sequence[tuple[str, str]]] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ):
        return self._table.select(
            columns=columns,
            where=self._merge_where(where),
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def select_one(self, columns: Optional[Sequence[str]] = None, where: Optional[FilterExpr] = None):
        return self._table.select_one(columns=columns, where=self._merge_where(where))

    def count(self, where: Optional[FilterExpr] = None) -> int:
        return self._table.count(where=self._merge_where(where))

    def exists(self, where: FilterExpr) -> bool:
        return self._table.exists(self._merge_where(where))

    def _inject_namespace(self, row: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self._has_namespace:
            return row
        if "namespace" in row and row["namespace"] != self._namespace:
            raise DatusException(
                ErrorCode.COMMON_FIELD_INVALID,
                message=f"namespace mismatch for table {self.name}: {row['namespace']} != {self._namespace}",
            )
        return {**row, "namespace": self._namespace}

    def _merge_where(self, where: Optional[FilterExpr]) -> FilterExpr:
        if not self._has_namespace:
            return where
        ns_filter = Condition("namespace", Op.EQ, self._namespace)
        if where is None:
            return ns_filter
        return And([ns_filter, where])

    def _namespace_conflict_columns(self, conflict_columns: Sequence[str]) -> Sequence[str]:
        if not self._has_namespace:
            return conflict_columns
        if "namespace" in conflict_columns:
            return conflict_columns
        return ("namespace", *conflict_columns)


class PostgresRelationalBackend(SQLAlchemyBackend):
    """PostgreSQL backend using SQLAlchemy Core with namespace scoping."""

    def __init__(
        self,
        db_path: str,
        db_name: str,
        connection_string: str,
        namespace: str,
        schema: str = "public",
        **backend_config: Any,
    ):
        engine_options = backend_config.get("engine_options") or {}
        connect_args = dict(engine_options.get("connect_args", {}))
        if schema:
            connect_args.setdefault("options", f"-c search_path={schema}")
        engine_options["connect_args"] = connect_args

        super().__init__(
            db_path=db_path,
            db_name=db_name,
            connection_string=connection_string,
            ddl_mode="disabled",
            engine_options=engine_options,
        )
        self._namespace = namespace
        self._schema = schema

    @property
    def name(self) -> str:
        return "postgresql"

    def ensure_table(self, schema: TableSchema) -> RelationalTable:
        table = self._reflect_table(schema.name)
        if table is None:
            raise DatusException(ErrorCode.DB_TABLE_NOT_EXISTS, message_args={"table_name": schema.name})

        self._validate_schema(schema, table_name=schema.name)
        table_handle = SQLAlchemyTable(self, table)
        has_namespace = "namespace" in table.c
        if not has_namespace:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={"error_message": f"Table {schema.name} missing required column: namespace"},
            )
        return NamespaceRelationalTable(table_handle, self._namespace, has_namespace)

    def table_exists(self, name: str) -> bool:
        inspector = inspect(self._connector.engine)
        try:
            return inspector.has_table(name, schema=self._schema)
        except SQLAlchemyError as exc:
            raise self._connector.handle_exception(exc, operation="inspect table") from exc

    def get_table(self, name: str) -> Optional[RelationalTable]:
        table = self._reflect_table(name)
        if table is None:
            return None
        table_handle = SQLAlchemyTable(self, table)
        return NamespaceRelationalTable(table_handle, self._namespace, "namespace" in table.c)

    def _reflect_table(self, name: str) -> Optional[Table]:
        if not self.table_exists(name):
            return None
        return Table(name, self._metadata, schema=self._schema, autoload_with=self._connector.engine)

    def _validate_schema(self, schema: TableSchema, table_name: str) -> None:
        inspector = inspect(self._connector.engine)
        try:
            columns = inspector.get_columns(table_name, schema=self._schema)
        except SQLAlchemyError as exc:
            raise self._connector.handle_exception(exc, operation="inspect columns") from exc
        existing = {col["name"] for col in columns}
        missing = [col.name for col in schema.columns if col.name not in existing]
        if missing:
            raise DatusException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={"error_message": f"Table {schema.name} missing columns: {', '.join(missing)}"},
            )
