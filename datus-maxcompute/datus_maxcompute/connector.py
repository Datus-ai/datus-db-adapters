# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from __future__ import annotations

import fnmatch
import itertools
import re
from typing import Any, Dict, Iterator, List, Literal, Optional, Sequence, Set, Tuple, Union, override

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
    from odps.errors import ODPSError, WaitTimeoutError
    from odps.rest import RestClient
    from odps.utils import escape_odps_string
except ImportError as exc:  # pragma: no cover - protected by package dependency
    ODPS = None  # type: ignore[assignment]
    RestClient = object  # type: ignore[assignment,misc]
    ODPSError = WaitTimeoutError = Exception  # type: ignore[misc,assignment]
    escape_odps_string = None  # type: ignore[assignment]
    _PYODPS_IMPORT_ERROR: Optional[Exception] = exc
else:
    _PYODPS_IMPORT_ERROR = None

logger = get_logger(__name__)

_SQL_PREVIEW_CHARS = 50
_NOT_THREE_LEVEL_MARKER = "is not 3-tier model project"
_TWO_LEVEL_MODEL_ERROR_CODE = "ODPS-0110061"
_TWO_LEVEL_MODEL_MARKER = "invalid database operations on two-tier model"
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
        ignore_table_patterns=get("ignore_table_patterns", default=[]),
    )


def _declares_partitions(table: Any) -> Optional[bool]:
    """Whether *table* declares partition columns, or ``None`` when that is unknown.

    Wrapped in a helper because the metadata read itself can fail on odd table types --
    foreign tables in particular. A failed read is reported as ``None`` rather than
    ``False``: it does not prove the table is unpartitioned, and assuming it is sends
    the caller down the predicate-free path that ``odps.sql.allow.fullscan=false``
    rejects, burning a job for a sample it never produces.
    """
    try:
        return bool(getattr(table.table_schema, "partitions", None))
    except Exception:  # noqa: BLE001 - unreadable schema: partition state unknown
        return None


def _matches_ignore_patterns(name: str, patterns: Sequence[str]) -> bool:
    """Whether *name* matches any entry of the ``ignore_table_patterns`` config.

    Patterns are globs (``fnmatch``), so a user can write ``tmp_*``, ``*_bak`` or
    ``tmp_2020*`` in ``agent.yml``. Matching is case-insensitive: MaxCompute object
    names are, and a pattern must not depend on the spelling the server returns.

    An empty pattern list -- the default -- disables filtering entirely. Nothing is
    ignored unless the user asks for it, so this adapter carries no assumption about
    which tables of a given project matter.
    """
    if not patterns:
        return False
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, str(pattern).lower()) for pattern in patterns)


# How many partitions to look at when picking a sample target. Partition listing is
# paged (100 per request), so this stays inside a single page: scanning it is free,
# while reading further pages costs one round-trip each.
_PARTITION_SCAN_LIMIT = 100


def _partition_has_data(partition: Any) -> bool:
    """Whether *partition* already holds data.

    ``physical_size`` comes straight out of the partition-listing JSON (pyodps parses
    it with a ``JSONNodeField``), so reading it costs no extra request. External tables
    -- OSS, Hologres FDW and friends -- do not report a physical size and yield
    ``None``; that is "unknown", not "empty", so it is reported as ``False`` and the
    caller keeps whatever candidate it has already seen.
    """
    size = getattr(partition, "physical_size", None)
    if size is None:
        return False
    try:
        return int(size) > 0
    except (TypeError, ValueError):
        return False


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
        message = str(exc).lower()
        if _NOT_THREE_LEVEL_MARKER in message:
            return True
        return getattr(exc, "code", None) == _TWO_LEVEL_MODEL_ERROR_CODE and _TWO_LEVEL_MODEL_MARKER in message

    def _ensure_namespace_mode(self) -> Literal["two_level", "three_level"]:
        if self._namespace_mode:
            return self._namespace_mode
        if self.namespace_mode_setting != "auto":
            self._namespace_mode = self.namespace_mode_setting
        else:
            try:
                list(self._odps.list_schemas(project=self.project))
                self._namespace_mode = "three_level"
            except ODPSError as exc:
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
        if _UNSUPPORTED_SQL_RE.match(sql_query):
            return ExecuteSQLResult(
                success=False,
                error="MaxCompute adapter does not support transactions or GRANT/REVOKE statements",
                sql_query=sql_query,
                result_format="",
            )
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

    @classmethod
    def _metadata_table_type(cls, table: Any) -> Literal["table", "view", "mv"]:
        object_type = cls._table_type(table)
        if object_type == "VIRTUAL_VIEW":
            return "view"
        if object_type == "MATERIALIZED_VIEW":
            return "mv"
        return "table"

    def _list_objects(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Tuple[str, str, List[Any]]:
        project, schema = self._validate_context(catalog_name, database_name, schema_name)
        objects = list(self._odps.list_tables(project=project, schema=schema or None))
        # Apply the ignore list *before* any DDL is fetched. Listing and the per-table
        # metadata read are separate calls, so a table a concurrent job drops in between
        # raises NoSuchObject from ``table.get_ddl()``. Inside a list comprehension that
        # aborts every remaining table of the datasource, throwing away metadata already
        # paid for. Filtering here also skips those reads altogether, and covers tables,
        # views and materialized views in one place.
        patterns = self.config.ignore_table_patterns
        if patterns:
            objects = [obj for obj in objects if not _matches_ignore_patterns(obj.name, patterns)]
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
        requested = set()
        for table_name in tables or []:
            requested_project, requested_schema, requested_name = self._resolve_table(
                table_name,
                database_name=project,
                schema_name=schema,
            )
            if (requested_project, requested_schema) != (project, schema):
                requested_scope = ".".join(part for part in (project, schema) if part)
                resolved_scope = ".".join(part for part in (requested_project, requested_schema) if part)
                raise DatusDbException(
                    ErrorCode.COMMON_INVALID_PARAMETER,
                    message_args={
                        "error_message": (
                            f"table '{table_name}' resolves to scope '{resolved_scope}', "
                            f"outside requested scope '{requested_scope}'"
                        )
                    },
                )
            requested.add(requested_name)
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

    @staticmethod
    def _sql_string_literal(value: Any) -> str:
        """Quote a partition value as a MaxCompute SQL string literal.

        Goes through pyodps' own escaping rather than doubling quotes by hand: ODPS
        reads backslash sequences (``\\n``, ``\\t``, ``\\b``) as escapes, so a value
        carrying a backslash would otherwise pin a different partition and quietly
        return no rows.
        """
        return "'" + escape_odps_string(str(value)) + "'"

    def _max_partition(self, table: Any) -> Any:
        """Newest partition of *table*, or ``None`` when none can be resolved.

        Reads **one page** of ``iterate_partitions(reverse=True)`` and keeps the best
        candidate found in it. The service returns partitions in its own reverse order,
        so the first entry is already the maximal one -- and unpadded numeric keys
        (``"10"`` before ``"9"``) are ranked by the service rather than by a string
        comparison here.

        Within that page the first partition that holds data wins; ``physical_size`` is
        read from the listing payload, not fetched per partition (see
        ``_partition_has_data``), so scanning the page costs nothing extra. When
        nothing on the page holds data -- or the table is external and reports no size
        at all -- the newest partition is returned anyway: an empty partition answers a
        predicate-pinned query with zero rows, which is a valid sample result.

        Why not ``table.get_max_partition()``: it materialises *every* partition of the
        table and sorts them in Python (pyodps ``models/partitions.py``)::

            part_values = [
                (part, tuple(part.partition_spec.values()))
                for part in self.iterate_partitions(spec)
            ]

        A 1042-partition table therefore costs ten paged round-trips instead of one
        (measured 1.0s vs 0.45s for the same partition), and an external table raises
        ``TypeError`` outright from its ``skip_empty`` ranking, which does
        ``part.physical_size > 0`` on a ``None``. Fewer requests also means fewer
        chances to land on a stalled network hop.

        Returns ``None`` when no partition can be resolved; the caller skips the table
        rather than falling back to a query that is guaranteed to be rejected.
        """
        try:
            candidates = itertools.islice(table.iterate_partitions(reverse=True), _PARTITION_SCAN_LIMIT)
            newest: Any = None
            for partition in candidates:
                if newest is None:
                    newest = partition
                if _partition_has_data(partition):
                    return partition
            return newest
        except Exception as exc:  # noqa: BLE001 - the caller skips this table instead
            logger.debug("Cannot iterate partitions of table %r: %s", getattr(table, "name", table), exc)
            return None

    def _sample_partition_predicate(self, table: Any) -> Optional[str]:
        """``WHERE`` clause pinning the newest partition, or ``""`` when none is needed.

        MaxCompute rejects an unqualified ``SELECT *`` on a partitioned table when the
        project sets ``odps.sql.allow.fullscan=false`` (a common hardening default on
        shared projects) with "is full scan with all partitions, please specify
        partition predicates" -- which left every partitioned table unsampleable.
        Sampling has to name a partition explicitly.

        The partition comes from ``_max_partition``, which asks the service for one page
        of partitions in reverse order. That keeps the service's own value ordering (so
        an unpadded numeric key such as ``"9"`` does not outrank ``"10"``) and prefers a
        partition that actually holds data, so a project whose latest partition has not
        been produced yet still yields a sample. Partition keys are quoted, since
        MaxCompute allows reserved words as partition-column names.

        Returns:
            ``""`` for tables that definitively declare no partitions -- no predicate
            required.
            ``" WHERE ..."`` when a partition could be pinned.
            ``None`` when the table may be partitioned but no partition could be
            resolved, including when its schema could not be read at all. The caller
            must skip such a table: an unqualified ``SELECT *`` would be rejected, so
            running it only burns a job and logs a traceback for a failure that is
            already known.
        """
        declares_partitions = _declares_partitions(table)
        if declares_partitions is None:
            return None
        if not declares_partitions:
            return ""

        partition = self._max_partition(table)
        spec = getattr(partition, "partition_spec", None)
        keys = getattr(spec, "keys", None)
        values = getattr(spec, "values", None)
        if not keys or not values:
            return None

        conditions = " AND ".join(
            f"{self.quote_identifier(k)}={self._sql_string_literal(v)}" for k, v in zip(keys, values)
        )
        return f" WHERE {conditions}"

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
        targets: List[Tuple[str, str, str, Literal["table", "view", "mv"], Any]] = []
        if tables:
            for table_name in tables:
                resolved_project, resolved_schema, name = self._resolve_table(
                    table_name,
                    database_name=project,
                    schema_name=schema,
                )
                table = self._odps.get_table(
                    name,
                    project=resolved_project,
                    schema=resolved_schema or None,
                )
                object_type = self._metadata_table_type(table)
                if table_type != "full" and table_type != object_type:
                    continue
                targets.append((resolved_project, resolved_schema, name, object_type, table))
        else:
            objects = list(self._odps.list_tables(project=project, schema=schema or None))
            for table in objects:
                object_type = self._metadata_table_type(table)
                if table_type == "full" or table_type == object_type:
                    targets.append((project, schema, table.name, object_type, table))

        result: List[Dict[str, Any]] = []
        for resolved_project, resolved_schema, name, object_type, table in targets:
            predicate = self._sample_partition_predicate(table)
            if predicate is None:
                # Partitioned table whose partition metadata could not be resolved.
                # An unqualified ``SELECT *`` would be rejected with ODPS-0130071, so
                # issuing it would only burn a job and log a traceback for a failure
                # that is already known. Skip the sample and keep the schema.
                logger.warning(
                    "Skipping sample of partitioned table %s: no partition predicate could be resolved", name
                )
                continue

            query = (
                f"SELECT * FROM "
                f"{self.full_name(database_name=resolved_project, schema_name=resolved_schema, table_name=name)}"
                f"{predicate} "
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
                    "table_type": object_type,
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
