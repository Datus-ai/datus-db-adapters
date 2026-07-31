# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Literal, Optional, Set, Tuple, Union, override

import pandas as pd
import pyarrow as pa
from pydantic import BaseModel, SecretStr

from datus_db_core import (
    TABLE_TYPE,
    BaseSqlConnector,
    DatusDbException,
    ErrorCode,
    ExecuteSQLResult,
    SQLType,
    get_logger,
    parse_sql_type,
)

from .config import MaxComputeConfig
from .handlers import parse_maxcompute_identifier

try:
    from odps import ODPS
    from odps.errors import InternalServerError, WaitTimeoutError
    from odps.rest import RestClient
except ImportError as exc:  # pragma: no cover - protected by package dependency
    ODPS = None  # type: ignore[assignment]
    RestClient = object  # type: ignore[assignment,misc]
    InternalServerError = WaitTimeoutError = Exception  # type: ignore[misc,assignment]
    _PYODPS_IMPORT_ERROR: Optional[Exception] = exc
else:
    _PYODPS_IMPORT_ERROR = None

logger = get_logger(__name__)

_SQL_PREVIEW_CHARS = 50
_NOT_THREE_LEVEL_MARKER = "is not 3-tier model project"
_UNSUPPORTED_SQL_RE = re.compile(
    r"^\s*(?:begin(?:\s+transaction)?|start\s+transaction|commit|rollback|grant|revoke)\b",
    flags=re.IGNORECASE,
)


def _log_sql_exception(event: str, sql: str, exc: Exception) -> None:
    """Log a SQL failure with a bounded preview instead of the full statement."""
    statement = sql or ""
    normalized_sql = " ".join(statement.split())
    sql_preview = normalized_sql[:_SQL_PREVIEW_CHARS]
    if len(normalized_sql) > _SQL_PREVIEW_CHARS:
        sql_preview += "..."
    driver_error = str(getattr(exc, "orig", None) or exc) or type(exc).__name__
    if statement:
        driver_error = driver_error.replace(statement, "<sql>")
    if normalized_sql and normalized_sql != statement:
        driver_error = driver_error.replace(normalized_sql, "<sql>")
    safe_exception = RuntimeError(driver_error)
    logger.error(
        "%s; sql_preview=%r; sql_chars=%d; error_type=%s; error=%s",
        event,
        sql_preview,
        len(statement),
        type(exc).__name__,
        driver_error,
        exc_info=(type(safe_exception), safe_exception, exc.__traceback__),
    )


def _secret_value(value: Union[SecretStr, str]) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


class _TimeoutRestClient(RestClient):
    """PyODPS REST client with connector-local request timeouts."""

    def __init__(self, *args, timeout_seconds: int = 30, **kwargs):
        self._request_timeout = (timeout_seconds, timeout_seconds)
        super().__init__(*args, **kwargs)

    def request(self, url, method, stream=False, **kwargs):
        kwargs.setdefault("timeout", self._request_timeout)
        return super().request(url, method, stream=stream, **kwargs)


def _coerce_config(config: Union[MaxComputeConfig, Dict[str, Any], BaseModel]) -> MaxComputeConfig:
    if isinstance(config, MaxComputeConfig):
        return config
    if isinstance(config, dict):
        return MaxComputeConfig.model_validate(config)

    def get(*names: str, default: Any = None) -> Any:
        for name in names:
            value = getattr(config, name, None)
            if value is not None and value != "" and not callable(value):
                return value
        return default

    return MaxComputeConfig(
        project=get("project", "database"),
        endpoint=get("endpoint"),
        access_key_id=get("access_key_id", "username"),
        access_key_secret=get("access_key_secret", "password"),
        schema=get("schema_name", "schema"),
        quota_name=get("quota_name"),
        tunnel_endpoint=get("tunnel_endpoint"),
        namespace_mode=get("namespace_mode", default="auto"),
        timeout_seconds=get("timeout_seconds", default=30),
        query_timeout_seconds=get("query_timeout_seconds", default=600),
        default_hints=get("default_hints", default={}),
    )


class MaxComputeConnector(BaseSqlConnector):
    """MaxCompute connector backed by the official PyODPS SDK."""

    def __init__(self, config: Union[MaxComputeConfig, Dict[str, Any], BaseModel]):
        if ODPS is None:
            raise DatusDbException(
                ErrorCode.COMMON_MISSING_DEPENDENCY,
                message_args={"dependency": "pyodps"},
            ) from _PYODPS_IMPORT_ERROR

        parsed_config = _coerce_config(config)
        super().__init__(parsed_config, dialect="maxcompute")
        self.config = parsed_config
        self.project = parsed_config.project
        self.endpoint = parsed_config.endpoint
        self.query_timeout_seconds = parsed_config.query_timeout_seconds
        self.quota_name = parsed_config.quota_name
        self.tunnel_endpoint = parsed_config.tunnel_endpoint
        self.namespace_mode_setting = parsed_config.namespace_mode
        self.default_hints = dict(parsed_config.default_hints)
        self._namespace_mode: Optional[Literal["two_level", "three_level"]] = None
        self._default_catalog = ""
        self._default_database = self.project
        self._default_schema = parsed_config.schema_name or ""
        self._odps = ODPS(
            _secret_value(parsed_config.access_key_id),
            _secret_value(parsed_config.access_key_secret),
            project=self.project,
            endpoint=self.endpoint,
            schema=parsed_config.schema_name,
            tunnel_endpoint=self.tunnel_endpoint,
            quota_name=self.quota_name,
            rest_client_cls=_TimeoutRestClient,
            rest_client_kwargs={"timeout_seconds": parsed_config.timeout_seconds},
        )
        self.connection = self._odps

    def connect(self):
        self.connection = self._odps
        self._ensure_namespace_mode()

    def close(self):
        # PyODPS uses request-scoped HTTP sessions and exposes no connection close.
        self.connection = None

    @staticmethod
    def _is_two_level_project_error(exc: Exception) -> bool:
        return _NOT_THREE_LEVEL_MARKER in str(exc).lower()

    def _ensure_namespace_mode(self) -> Literal["two_level", "three_level"]:
        if self._namespace_mode:
            return self._namespace_mode
        if self.namespace_mode_setting != "auto":
            self._namespace_mode = self.namespace_mode_setting
        else:
            try:
                list(self._odps.list_schemas(project=self.project))
                self._namespace_mode = "three_level"
            except InternalServerError as exc:
                if not self._is_two_level_project_error(exc):
                    raise
                self._namespace_mode = "two_level"

        if self._namespace_mode == "three_level":
            self._default_schema = self.config.schema_name or "default"
        else:
            self._default_schema = ""
        return self._namespace_mode

    @property
    def namespace_mode(self) -> Literal["two_level", "three_level"]:
        return self._ensure_namespace_mode()

    @override
    def get_effective_capabilities(self) -> Set[str]:
        capabilities = {"database"}
        if self.namespace_mode == "three_level":
            capabilities.add("schema")
        return capabilities

    @override
    def get_current_context(self) -> Dict[str, str]:
        self.connect()
        return {
            "catalog_name": "",
            "database_name": self.project,
            "schema_name": self.schema_name if self.namespace_mode == "three_level" else "",
        }

    def _validate_context(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Tuple[str, str]:
        if catalog_name:
            raise DatusDbException(
                ErrorCode.COMMON_UNSUPPORTED,
                message=f"MaxCompute does not expose a catalog namespace: {catalog_name}",
            )
        project = database_name or self.project
        if project != self.project:
            raise DatusDbException(
                ErrorCode.COMMON_UNSUPPORTED,
                message=(
                    f"This datasource is bound to MaxCompute project '{self.project}'; "
                    f"cross-project access to '{project}' is not supported"
                ),
            )

        if self.namespace_mode == "two_level":
            if schema_name:
                raise DatusDbException(
                    ErrorCode.COMMON_UNSUPPORTED,
                    message=f"Project '{self.project}' uses project.table and has no schema namespace",
                )
            return project, ""
        return project, schema_name or self.schema_name or "default"

    @override
    def do_switch_context(
        self,
        conn: Any,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ):
        self._validate_context(catalog_name, database_name, schema_name)

    def _job_hints(self) -> Dict[str, Any]:
        hints = dict(self.default_hints)
        hints["odps.namespace.schema"] = "true" if self.namespace_mode == "three_level" else "false"
        return hints

    @staticmethod
    def _reject_unsupported_sql(sql: str):
        if _UNSUPPORTED_SQL_RE.match(sql):
            raise DatusDbException(
                ErrorCode.COMMON_UNSUPPORTED,
                message="MaxCompute adapter does not support transactions or GRANT/REVOKE statements",
            )

    def _run_instance(
        self,
        sql: str,
        database_name: str = "",
        schema_name: str = "",
        catalog_name: str = "",
    ):
        self._reject_unsupported_sql(sql)
        project, schema = self._validate_context(catalog_name, database_name, schema_name)
        kwargs: Dict[str, Any] = {
            "project": project,
            "hints": self._job_hints(),
        }
        if schema:
            kwargs["default_schema"] = schema
        if self.quota_name:
            kwargs["quota_name"] = self.quota_name
        instance = self._odps.run_sql(sql, **kwargs)
        try:
            instance.wait_for_success(timeout=self.query_timeout_seconds)
        except (WaitTimeoutError, TimeoutError) as exc:
            try:
                instance.stop()
            finally:
                raise DatusDbException(
                    ErrorCode.DB_EXECUTION_TIMEOUT,
                    message_args={"error_message": str(exc)},
                ) from exc
        return instance

    def _read_arrow(self, instance, max_rows: Optional[int] = None) -> pa.Table:
        reader_kwargs: Dict[str, Any] = {
            "tunnel": True,
            "arrow": True,
            "limit": False,
            "timeout": self.timeout_seconds,
        }
        if self.tunnel_endpoint:
            reader_kwargs["endpoint"] = self.tunnel_endpoint
        if self.quota_name:
            reader_kwargs["quota_name"] = self.quota_name
        with instance.open_reader(**reader_kwargs) as reader:
            return reader.read_all(count=max_rows)

    def _read_task_result(self, instance, sql_type: SQLType, max_rows: Optional[int] = None) -> pa.Table:
        """Read text results produced by non-SELECT MaxCompute SQL jobs."""
        raw_result = str(instance.get_task_result() or "")
        if sql_type == SQLType.EXPLAIN:
            stripped_result = raw_result.strip("\r\n")
            values = [stripped_result] if stripped_result.strip() else []
        else:
            values = [line.rstrip() for line in raw_result.splitlines() if line.strip()]
        if max_rows is not None:
            values = values[:max_rows]
        return pa.table({"result": pa.array(values, type=pa.string())})

    def _query_arrow(
        self,
        sql: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        max_rows: Optional[int] = None,
    ) -> pa.Table:
        instance = self._run_instance(
            sql,
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
        )
        sql_type = parse_sql_type(sql, self.dialect)
        if sql_type in {SQLType.METADATA_SHOW, SQLType.EXPLAIN}:
            return self._read_task_result(instance, sql_type, max_rows=max_rows)
        return self._read_arrow(instance, max_rows=max_rows)

    @staticmethod
    def _query_result(sql: str, table: pa.Table, result_format: str) -> ExecuteSQLResult:
        if result_format == "arrow":
            sql_return: Any = table
        elif result_format == "pandas":
            sql_return = table.to_pandas()
        elif result_format == "list":
            sql_return = table.to_pylist()
        else:
            sql_return = table.to_pandas().to_csv(index=False)
        return ExecuteSQLResult(
            success=True,
            sql_query=sql,
            sql_return=sql_return,
            row_count=table.num_rows,
            result_format=result_format,
        )

    @override
    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        try:
            table = self._query_arrow(sql, catalog_name, database_name, schema_name)
            return self._query_result(sql, table, result_format)
        except Exception as exc:
            _log_sql_exception("MaxCompute query execution failed", sql, exc)
            return ExecuteSQLResult(
                success=False,
                error=str(exc),
                sql_query=sql,
                result_format=result_format,
            )

    @override
    def execute_explain(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format, catalog_name, database_name, schema_name)

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="arrow")

    @override
    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="pandas")

    @override
    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        return self.execute_query(sql, result_format="csv")

    def _execute_non_query(
        self,
        sql: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        try:
            instance = self._run_instance(sql, database_name, schema_name, catalog_name)
            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=instance.id,
                row_count=None,
                result_format="",
            )
        except Exception as exc:
            _log_sql_exception("MaxCompute SQL execution failed", sql, exc)
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql, result_format="")

    @override
    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self._execute_non_query(sql, catalog_name, database_name, schema_name)

    @override
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self._execute_non_query(sql, catalog_name, database_name, schema_name)

    @override
    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self._execute_non_query(sql, catalog_name, database_name, schema_name)

    @override
    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self._execute_non_query(sql, catalog_name, database_name, schema_name)

    @override
    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(
            success=False,
            error="MaxCompute context switching SQL is not supported; pass project/schema as execution context",
            sql_query=sql_query,
            result_format="",
        )

    @override
    def execute_queries(self, queries: List[str]) -> List[ExecuteSQLResult]:
        return [self.execute({"sql_query": sql}) for sql in queries]

    @override
    def execute_csv_iterator(
        self, query: str, max_rows: int = 100, with_header: bool = True
    ) -> Iterator[Tuple[str, ...]]:
        try:
            table = self._query_arrow(query, max_rows=max(0, int(max_rows)))
        except Exception as exc:
            _log_sql_exception("MaxCompute query execution failed", query, exc)
            raise DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message=str(exc)) from exc
        if with_header:
            yield tuple(table.column_names)
        for row in table.to_pylist():
            yield tuple("" if row[name] is None else str(row[name]) for name in table.column_names)

    @override
    def test_connection(self):
        self.connect()
        self._odps.get_project(self.project).reload()
        return True

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        self._validate_context(catalog_name=catalog_name)
        return [self.project]

    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        project, _ = self._validate_context(catalog_name, database_name)
        if self.namespace_mode == "two_level":
            return []
        return [schema.name for schema in self._odps.list_schemas(project=project)]

    @staticmethod
    def _table_type(table: Any) -> str:
        value = getattr(table, "type", "")
        return str(getattr(value, "value", value)).upper()

    def _list_objects(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Tuple[str, str, List[Any]]:
        project, schema = self._validate_context(catalog_name, database_name, schema_name)
        objects = list(self._odps.list_tables(project=project, schema=schema or None))
        return project, schema, objects

    @staticmethod
    def _qualify_listed_name(
        name: str,
        project: str,
        schema: str,
        requested_project: str,
        requested_schema: str,
    ) -> str:
        if schema and requested_project and not requested_schema:
            # MaxCompute parses every two-part identifier as project.table.
            # Keep the three-level listing unambiguous and round-trippable.
            return ".".join((project, schema, name))
        parts = []
        if not requested_project:
            parts.append(project)
        if schema and not requested_schema:
            parts.append(schema)
        parts.append(name)
        return ".".join(parts)

    @override
    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        return [
            self._qualify_listed_name(table.name, project, schema, database_name, schema_name)
            for table in objects
            if self._table_type(table) not in {"VIRTUAL_VIEW", "MATERIALIZED_VIEW"}
        ]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        return [
            self._qualify_listed_name(table.name, project, schema, database_name, schema_name)
            for table in objects
            if self._table_type(table) == "VIRTUAL_VIEW"
        ]

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        return [
            self._qualify_listed_name(table.name, project, schema, database_name, schema_name)
            for table in objects
            if self._table_type(table) == "MATERIALIZED_VIEW"
        ]

    def _resolve_table(
        self,
        table_name: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Tuple[str, str, str]:
        parsed = parse_maxcompute_identifier(table_name)
        parsed_project = parsed["database_name"]
        parsed_schema = parsed["schema_name"]
        project, schema = self._validate_context(
            catalog_name,
            parsed_project or database_name,
            parsed_schema or schema_name,
        )
        return project, schema, parsed["table_name"]

    def _get_table(
        self,
        table_name: str,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ):
        project, schema, name = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        return project, schema, self._odps.get_table(name, project=project, schema=schema or None)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        if not table_name:
            return []
        _, _, table = self._get_table(table_name, catalog_name, database_name, schema_name)
        table.reload()
        partitions = {column.name for column in table.table_schema.partitions}
        result = []
        for index, column in enumerate(table.table_schema.columns):
            result.append(
                {
                    "cid": index,
                    "name": column.name,
                    "type": str(column.type),
                    "comment": getattr(column, "comment", None),
                    "nullable": getattr(column, "nullable", True) is not False,
                    "pk": False,
                    "default_value": None,
                    "is_partition": column.name in partitions,
                }
            )
        return result

    def _metadata_entry(self, project: str, schema: str, table: Any, table_type: str) -> Dict[str, str]:
        return {
            "identifier": self.identifier(
                database_name=project,
                schema_name=schema,
                table_name=table.name,
            ),
            "catalog_name": "",
            "database_name": project,
            "schema_name": schema,
            "table_name": table.name,
            "table_type": table_type,
            "definition": table.get_ddl(),
        }

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        requested = {self._resolve_table(name, database_name=project, schema_name=schema)[2] for name in tables or []}
        return [
            self._metadata_entry(project, schema, table, "table")
            for table in objects
            if self._table_type(table) not in {"VIRTUAL_VIEW", "MATERIALIZED_VIEW"}
            and (not requested or table.name in requested)
        ]

    @override
    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        return [
            self._metadata_entry(project, schema, table, "view")
            for table in objects
            if self._table_type(table) == "VIRTUAL_VIEW"
        ]

    def get_materialized_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        project, schema, objects = self._list_objects(catalog_name, database_name, schema_name)
        return [
            self._metadata_entry(project, schema, table, "mv")
            for table in objects
            if self._table_type(table) == "MATERIALIZED_VIEW"
        ]

    @override
    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, Any]]:
        project, schema = self._validate_context(catalog_name, database_name, schema_name)
        table_names = tables or self.get_tables(database_name=project, schema_name=schema)
        result: List[Dict[str, Any]] = []
        for table_name in table_names:
            resolved_project, resolved_schema, name = self._resolve_table(
                table_name,
                database_name=project,
                schema_name=schema,
            )
            query = (
                f"SELECT * FROM "
                f"{self.full_name(database_name=resolved_project, schema_name=resolved_schema, table_name=name)} "
                f"LIMIT {int(top_n)}"
            )
            query_result = self.execute_query(
                query,
                result_format="pandas",
                database_name=resolved_project,
                schema_name=resolved_schema,
            )
            if not query_result.success:
                logger.warning("Failed to sample MaxCompute table %s: %s", name, query_result.error)
                continue
            frame: pd.DataFrame = query_result.sql_return
            result.append(
                {
                    "identifier": self.identifier(
                        database_name=resolved_project,
                        schema_name=resolved_schema,
                        table_name=name,
                    ),
                    "catalog_name": "",
                    "database_name": resolved_project,
                    "schema_name": resolved_schema,
                    "table_name": name,
                    "table_type": table_type,
                    "sample_rows": frame.to_csv(index=False),
                }
            )
        return result

    @override
    def quote_identifier(self, name: str) -> str:
        return f"`{name.replace('`', '``')}`"

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        project, schema, name = self._resolve_table(table_name, catalog_name, database_name, schema_name)
        parts = [project]
        if schema:
            parts.append(schema)
        parts.append(name)
        return ".".join(self.quote_identifier(part) for part in parts)
