# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Literal, Optional, Set, Tuple

from datus_db_core.config import ConnectionConfig
from datus_db_core.constants import SQLType
from datus_db_core.logging import get_logger
from datus_db_core.models import TABLE_TYPE, ExecuteSQLInput, ExecuteSQLResult
from datus_db_core.sql_utils import metadata_identifier, parse_sql_type

logger = get_logger(__name__)


_UNSET = object()


class BaseSqlConnector(ABC):
    def __new__(cls, *args, **kwargs):
        instance = super().__new__(cls)
        # ContextVar: works with both threading and asyncio (per-task isolation).
        # Always initialized in __new__ so it survives mocked __init__ in tests.
        instance._catalog_var = ContextVar(f"catalog_{id(instance)}", default=_UNSET)
        instance._database_var = ContextVar(f"database_{id(instance)}", default=_UNSET)
        instance._schema_var = ContextVar(f"schema_{id(instance)}", default=_UNSET)
        instance._default_catalog = ""
        instance._default_database = ""
        instance._default_schema = ""
        return instance

    def __init__(self, config: ConnectionConfig, dialect: str):
        self.config = config
        self.timeout_seconds = config.timeout_seconds
        self.dialect = dialect

    # --- Context-isolated properties ---
    # Each thread/async-task sees its own catalog/database/schema values.
    # Unset contexts fall back to the defaults set during __init__.

    @property
    def catalog_name(self) -> str:
        val = self._catalog_var.get()
        return val if val is not _UNSET else self._default_catalog

    @catalog_name.setter
    def catalog_name(self, value: str):
        self._catalog_var.set(value)

    @property
    def database_name(self) -> str:
        val = self._database_var.get()
        return val if val is not _UNSET else self._default_database

    @database_name.setter
    def database_name(self, value: str):
        self._database_var.set(value)

    @property
    def schema_name(self) -> str:
        val = self._schema_var.get()
        return val if val is not _UNSET else self._default_schema

    @schema_name.setter
    def schema_name(self, value: str):
        self._schema_var.set(value)

    def get_current_context(self) -> Dict[str, str]:
        """Return the connector's effective SQL context.

        The returned names are SQL-addressing coordinates, not Datus datasource
        routing keys. Dialect-specific connectors may override this method to
        normalize aliases or fill dialect defaults.
        """
        return {
            "catalog_name": self.catalog_name or "",
            "database_name": self.database_name or "",
            "schema_name": self.schema_name or "",
        }

    def close(self):
        pass

    def connect(self):
        return

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.close()
        except Exception as e:
            logger.warning(f"Failed to close connection during cleanup: {e}")
        return False

    def _safe_rollback(self):
        if hasattr(self, "connection") and self.connection:
            try:
                if hasattr(self.connection, "rollback"):
                    self.connection.rollback()
            except Exception:
                pass

    def quote_identifier(self, name: str) -> str:
        """Quote an identifier using the dialect-appropriate quoting character.

        Default uses ANSI SQL double quotes. Subclasses should override
        for dialects that use a different quoting style (e.g. backticks).
        """
        escaped = name.replace('"', '""')
        return f'"{escaped}"'

    def execute(
        self,
        input_params: Any,
        result_format: Optional[Literal["csv", "arrow", "pandas", "list"]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        self.validate_input(input_params)
        if isinstance(input_params, dict):
            input_params = ExecuteSQLInput(**input_params)
        sql_query = input_params.sql_query.strip()
        if result_format is None:
            result_format = getattr(input_params, "result_format", "csv") or "csv"
        # Only pass context kwargs when explicitly set (backward compatible)
        ctx = {
            k: v
            for k, v in [("catalog_name", catalog_name), ("database_name", database_name), ("schema_name", schema_name)]
            if v
        }
        try:
            sql_type = parse_sql_type(sql_query, self.dialect)
            if sql_type == SQLType.INSERT:
                result = self._call_with_ctx(self.execute_insert, sql_query, ctx)
            elif sql_type in (SQLType.UPDATE, SQLType.MERGE):
                result = self._call_with_ctx(self.execute_update, sql_query, ctx)
            elif sql_type == SQLType.DELETE:
                result = self._call_with_ctx(self.execute_delete, sql_query, ctx)
            elif sql_type == SQLType.CONTENT_SET:
                result = self.execute_content_set(sql_query)
            elif sql_type == SQLType.DDL:
                result = self._call_with_ctx(self.execute_ddl, sql_query, ctx)
            elif sql_type == SQLType.SELECT:
                result = self._call_with_ctx(self.execute_query, sql_query, ctx, result_format)
            elif sql_type == SQLType.METADATA_SHOW:
                result = self._call_with_ctx(self.execute_query, sql_query, ctx, result_format)
            elif sql_type == SQLType.EXPLAIN:
                result = self._call_with_ctx(self.execute_explain, sql_query, ctx, result_format)
            else:
                return ExecuteSQLResult(
                    success=False,
                    error="Unknown type of SQL",
                    sql_query=sql_query,
                    sql_return="",
                    row_count=0,
                    result_format=result_format,
                )

            return result
        except Exception as e:
            logger.error(f"Executing SQL query failed: {e}")
            return ExecuteSQLResult(
                success=False,
                error=str(e),
                sql_query=sql_query,
                sql_return="",
                row_count=0,
                result_format=result_format,
            )

    @abstractmethod
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        raise NotImplementedError()

    def validate_input(self, input_params: Any):
        if isinstance(input_params, dict):
            if "sql_query" not in input_params:
                raise ValueError("'sql_query' parameter is required")
            if not isinstance(input_params["sql_query"], str):
                raise ValueError("'sql_query' must be a string")
        else:
            if not hasattr(input_params, "sql_query"):
                raise ValueError("'sql_query' parameter is required")
            if not isinstance(input_params.sql_query, str):
                raise ValueError("'sql_query' must be a string")

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        raise NotImplementedError()

    def execute_explain(
        self, sql: str, result_format: Literal["csv", "arrow", "pandas", "list"] = "csv"
    ) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format)

    @abstractmethod
    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        raise NotImplementedError()

    @abstractmethod
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        raise NotImplementedError()

    @abstractmethod
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        raise NotImplementedError()

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        return []

    def _sys_databases(self) -> Set[str]:
        return set()

    def _sys_schemas(self) -> Set[str]:
        return set()

    def execute_csv_iterator(
        self, query: str, max_rows: int = 100, with_header: bool = True
    ) -> Iterator[Tuple[str, ...]]:
        raise NotImplementedError()

    @abstractmethod
    def test_connection(self):
        raise NotImplementedError()

    def get_type(self) -> str:
        return self.dialect

    def get_effective_capabilities(self) -> Set[str]:
        """Return capabilities for this configured connector instance.

        Most adapters use their registered static capabilities. Adapters whose
        namespace model depends on the connected service may override this
        method without requiring callers to know the database type.
        """
        from datus_db_core.registry import connector_registry

        return connector_registry.get_capabilities(self.dialect)

    @abstractmethod
    def execute_queries(self, queries: List[str]) -> List[Any]:
        raise NotImplementedError()

    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        raise NotImplementedError()

    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        filter_tables = []
        if tables:
            catalog_name = catalog_name or self.catalog_name
            database_name = database_name or self.database_name
            schema_name = schema_name or self.schema_name
            for table_name in tables:
                filter_tables.append(
                    self.full_name(
                        table_name=table_name,
                        catalog_name=catalog_name,
                        database_name=database_name,
                        schema_name=schema_name,
                    )
                )
        return filter_tables

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        raise NotImplementedError()

    @staticmethod
    def _call_with_ctx(method, sql, ctx, *args, **extra_kwargs):
        """Call an execute method with context kwargs, raising if unsupported."""
        if ctx:
            try:
                return method(sql, *args, **extra_kwargs, **ctx)
            except TypeError as e:
                if "unexpected keyword argument" in str(e):
                    raise TypeError(
                        f"{method.__qualname__} does not accept per-call context overrides: {sorted(ctx)}"
                    ) from e
                raise
        return method(sql, *args, **extra_kwargs)

    def switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Update context state and apply to live session if applicable.

        For SQLAlchemy connectors: context is applied per-operation via _conn().
        For native connectors (Redshift/Snowflake): also calls _apply_live_context()
        to execute USE/SET on the persistent connection.

        State is updated only after _apply_live_context succeeds, so a failed
        live switch does not leave thread-local context out of sync.
        """
        self._apply_live_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        if catalog_name:
            self.catalog_name = catalog_name
        if database_name:
            self.database_name = database_name
        if schema_name:
            self.schema_name = schema_name

    def _apply_live_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply context to a persistent connection (for native connectors).

        Auto-detects connectors with persistent connections (e.g., Redshift,
        Snowflake) and calls their old-style do_switch_context() to execute
        USE/SET on the live session.
        """
        connection = getattr(self, "connection", None)
        if connection is not None:
            # Native connector with persistent connection — call old-style do_switch_context
            import inspect as _inspect

            sig = _inspect.signature(self.do_switch_context)
            params = list(sig.parameters.keys())
            if params and params[0] != "conn":
                # Old signature: do_switch_context(self, catalog_name, database_name, schema_name)
                self.do_switch_context(
                    catalog_name=catalog_name,
                    database_name=database_name,
                    schema_name=schema_name,
                )
            else:
                # New signature: do_switch_context(self, conn, ...)
                self.do_switch_context(
                    connection,
                    catalog_name=catalog_name,
                    database_name=database_name,
                    schema_name=schema_name,
                )

    def do_switch_context(self, conn: Any, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply context (USE/SET CATALOG) to a given connection. Subclasses override."""
        return None

    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, str]]:
        raise NotImplementedError()

    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError()

    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        raise NotImplementedError()

    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        return metadata_identifier(
            dialect=self.dialect,
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
        )

    @abstractmethod
    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        raise NotImplementedError()


def list_to_in_str(prefix: str, values: Optional[List[str]] = None) -> str:
    if not values:
        return ""
    value_str = ",".join(to_sql_literal(v, around_with_quotes=True) for v in values)
    return f"{prefix} ({value_str})"


def _escape_sql_string_standard(value: str) -> str:
    return value.replace("'", "''")


def to_sql_literal(value: Optional[str], around_with_quotes: bool = False) -> str:
    if value is None:
        return "NULL"
    if not value:
        return "" if not around_with_quotes else "''"
    replace_value = _escape_sql_string_standard(value)
    if not around_with_quotes:
        return replace_value
    else:
        return f"'{replace_value}'"
