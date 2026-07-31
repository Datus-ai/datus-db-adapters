# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
import pyarrow as pa

# Legacy connector - does not inherit from BaseSqlConnector directly
from datus_db_core import (
    TABLE_TYPE,
    DatusDbException,
    ErrorCode,
    ExecuteSQLResult,
    SQLType,
    get_logger,
    metadata_identifier,
    parse_context_switch,
    parse_sql_type,
)

try:
    from clickzetta.zettapark.session import Session
except ImportError as exc:  # pragma: no cover - optional dependency
    Session = None  # type: ignore[assignment]
    _CLICKZETTA_IMPORT_ERROR: Optional[Exception] = exc
else:
    _CLICKZETTA_IMPORT_ERROR = None

logger = get_logger(__name__)

_SQL_PREVIEW_CHARS = 50
_DEFAULT_HINTS: Dict[str, Any] = {
    "sdk.job.timeout": 300,
    "query_tag": "Query from Datus Agent",
    "cz.storage.parquet.vector.index.read.memory.cache": "true",
    "cz.storage.parquet.vector.index.read.local.cache": "false",
    "cz.sql.table.scan.push.down.filter": "true",
    "cz.sql.table.scan.enable.ensure.filter": "true",
    "cz.storage.always.prefetch.internal": "true",
    "cz.optimizer.generate.columns.always.valid": "true",
    "cz.sql.index.prewhere.enabled": "true",
    "cz.storage.parquet.enable.io.prefetch": "false",
}


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


def _safe_escape(value: Optional[str]) -> str:
    """Escape single quotes in string values for SQL literals."""
    if value is None:
        return ""
    return value.replace("'", "''")


def _safe_escape_identifier(identifier: Optional[str]) -> str:
    """Escape backticks in identifiers for SQL identifiers using backtick quoting."""
    if identifier is None:
        return ""
    return str(identifier).replace("`", "``")


class ClickZettaConnector:
    """
    Connector implementation for ClickZetta Lakehouse.
    Wraps the official `clickzetta.zettapark` session with the BaseSqlConnector interface.
    """

    AUTH_EXPIRATION_SECONDS = 3600

    def __init__(
        self,
        service: str,
        username: str,
        password: str,
        instance: str,
        workspace: str,
        schema: str = "",
        vcluster: str = "",
        secure: Optional[bool] = None,
        hints: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        # Initialize minimal attributes without calling parent's __init__
        from datus_db_core import ConnectionConfig

        self.config = ConnectionConfig()
        self.timeout_seconds = 30
        self.connection = None
        self.dialect = "clickzetta"
        self.db_type = "clickzetta"
        schema = schema or "PUBLIC"
        vcluster = vcluster or "DEFAULT_AP"
        if Session is None:
            raise DatusDbException(
                ErrorCode.COMMON_MISSING_DEPENDENCY,
                message=(
                    "ClickZetta connector requires the packages "
                    "`clickzetta-connector-python` and `clickzetta-zettapark-python`. "
                    "Please install them before using the connector."
                ),
            ) from _CLICKZETTA_IMPORT_ERROR

        required_fields = {
            "service": service,
            "username": username,
            "password": password,
            "instance": instance,
            "workspace": workspace,
            "schema": schema,
            "vcluster": vcluster,
        }
        missing_fields = [name for name, value in required_fields.items() if not value]
        if missing_fields:
            raise DatusDbException(
                ErrorCode.COMMON_CONFIG_ERROR,
                message_args={
                    "config_error": f"Missing ClickZetta connection fields: {', '.join(sorted(missing_fields))}"
                },
            )

        self.service = service
        self.user = username
        self.password = password
        self.instance = instance
        self._workspace = workspace
        self.schema_name = schema
        self.vcluster = vcluster

        self.catalog_name = ""
        self.database_name = workspace or ""

        merged_hints: Dict[str, Any] = dict(_DEFAULT_HINTS)
        if hints:
            merged_hints.update(hints)

        connection_config: Dict[str, Any] = {
            "service": service,
            "username": username,
            "password": password,
            "instance": instance,
            "workspace": workspace,
            "schema": self.schema_name,
            "vcluster": self.vcluster,
            "hints": merged_hints,
        }
        if secure is not None:
            connection_config["secure"] = secure
        if extra:
            connection_config.update(extra)

        self._connection_config = connection_config
        # Note: avoid referencing Session in type annotations to prevent runtime/type issues when optional dep missing
        self._session: Optional[Any] = None
        self._auth_timestamp = 0.0

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _ensure_connection(self) -> Any:
        if self._session is None or (time.time() - self._auth_timestamp) > self.AUTH_EXPIRATION_SECONDS:
            self.connect()
        return self._session  # type: ignore[return-value]

    def connect(self):
        """Initialize or refresh the ClickZetta session."""
        try:
            self._session = Session.builder.configs(self._connection_config).create()
            self._auth_timestamp = time.time()
            self.connection = self._session  # Maintain BaseSqlConnector.connection reference
            if self.schema_name:
                escaped_schema = _safe_escape_identifier(self.schema_name.upper())
                self._session.sql(f"USE SCHEMA `{escaped_schema}`")
            if self.vcluster:
                escaped_vc = _safe_escape_identifier(self.vcluster.upper())
                self._session.sql(f"USE VCLUSTER `{escaped_vc}`")
        except (ImportError, OSError, ValueError) as exc:
            raise DatusDbException(
                ErrorCode.DB_CONNECTION_FAILED,
                message_args={"error_message": str(exc)},
            ) from exc

    def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.debug(f"Failed to close ClickZetta session cleanly: {exc}")
            finally:
                self._session = None
                self.connection = None

    def do_switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Execute context switching in ClickZetta session.

        Note:
        - Workspace (database_name) switching is NOT supported and requires separate namespace configuration
        - Only schema switching within the same workspace is supported
        - VCluster switching is supported via USE VCLUSTER command
        """
        session = self._ensure_connection()

        # Reject workspace switching requests
        if database_name and database_name != self.database_name:
            raise DatusDbException(
                ErrorCode.DB_EXECUTION_ERROR,
                message_args={
                    "error_message": (
                        f"Workspace switching from '{self.database_name}' to '{database_name}' "
                        "is not supported. Please configure separate namespaces for different workspaces."
                    ),
                    "sql": f"USE DATABASE {database_name}",
                },
            )

        # Support schema switching within the same workspace
        if schema_name and schema_name != self.schema_name:
            try:
                escaped_schema = _safe_escape_identifier(schema_name.upper())
                session.sql(f"USE SCHEMA `{escaped_schema}`")
                # Update the connector's schema state after successful execution
                self.schema_name = schema_name
                logger.info(f"Switched to schema: {schema_name}")
            except (OSError, ValueError, RuntimeError) as exc:
                escaped_schema = _safe_escape_identifier(schema_name.upper())
                self._wrap_exception(exc, f"USE SCHEMA `{escaped_schema}`", ErrorCode.DB_EXECUTION_ERROR)

    def _wrap_exception(
        self,
        exc: Exception,
        sql: str = "",
        error_code: ErrorCode = ErrorCode.DB_EXECUTION_ERROR,
    ):
        if isinstance(exc, DatusDbException):
            raise exc
        _log_sql_exception("ClickZetta SQL execution failed", sql, exc)
        raise DatusDbException(error_code, message_args={"error_message": str(exc), "sql": sql}) from exc

    def _run_query(self, sql: str) -> pd.DataFrame:
        try:
            session = self._ensure_connection()
            result = session.sql(sql)
            if hasattr(result, "to_pandas"):
                return result.to_pandas()
            # Fallback to empty DataFrame if result has no tabular output
            return pd.DataFrame()
        except (OSError, ValueError, RuntimeError) as exc:
            self._wrap_exception(exc, sql)
            raise  # unreachable, but satisfies type checkers

    def _run_command(self, sql: str) -> pd.DataFrame:
        try:
            session = self._ensure_connection()
            result = session.sql(sql)
            if hasattr(result, "to_pandas"):
                try:
                    return result.to_pandas()
                except (AttributeError, TypeError, ValueError):
                    return pd.DataFrame()
            return pd.DataFrame()
        except (OSError, ValueError, RuntimeError) as exc:
            self._wrap_exception(exc, sql)
            raise  # unreachable, but satisfies type checkers

    @staticmethod
    def _normalize_volume_uri(volume: str, relative_path: str) -> str:
        base = (volume or "").strip()
        if not base:
            raise ValueError("Volume name must not be empty when reading semantic model files.")
        if base.lower().startswith("volume:"):
            base = base.rstrip("/")
            relative = (relative_path or "").lstrip("/")
            return f"{base}/{relative}" if relative else base
        if base.startswith("@"):
            relative = (relative_path or "").lstrip("/")
            return f"{base.rstrip('/')}/{relative}" if relative else base
        raise ValueError(f"Unsupported volume/stage format: {volume}")

    def read_volume_file(self, volume: str, relative_path: str) -> str:
        """Download and return the contents of a file stored inside a ClickZetta volume or stage."""
        source_uri = self._normalize_volume_uri(volume, relative_path)
        session = self._ensure_connection()

        with tempfile.TemporaryDirectory() as tmp_dir:
            session.file.get(source_uri, tmp_dir)

            candidate = Path(tmp_dir) / Path(relative_path).name
            if not candidate.exists():
                nested_candidate = Path(tmp_dir) / Path(relative_path)
                if nested_candidate.exists():
                    candidate = nested_candidate
                else:
                    matches = list(Path(tmp_dir).rglob(Path(relative_path).name))
                    if not matches:
                        raise FileNotFoundError(f"File '{relative_path}' not found in {volume}")
                    candidate = matches[0]

            return candidate.read_text(encoding="utf-8")

    def list_volume_files(
        self,
        volume: str,
        directory: str = "",
        suffixes: tuple[str, ...] = (".yaml", ".yml"),
    ) -> List[str]:
        """List files stored inside a ClickZetta volume or legacy stage."""
        directory = directory.strip().lstrip("/").rstrip("/")
        volume_uri = self._normalize_volume_uri(volume, directory or "")
        session = self._ensure_connection()

        if volume.lower().startswith("volume:user://"):
            list_sql = "LIST USER VOLUME"
            if directory:
                # Escape single quotes to avoid breaking the SQL literal
                escaped_dir = _safe_escape(directory.rstrip("/"))
                list_sql += f" SUBDIRECTORY '{escaped_dir}/'"
        else:
            list_sql = f"LIST {volume_uri}"

        result = session.sql(list_sql)
        try:
            df = result.to_pandas()
        except (AttributeError, TypeError, ValueError):
            df = pd.DataFrame()

        if df.empty:
            return []

        column_name = None
        for candidate in ("relative_path", "name", "path"):
            if candidate in df.columns:
                column_name = candidate
                break
        if column_name is None:
            column_name = df.columns[0]

        discovered: List[str] = []
        for value in df[column_name].tolist():
            if not value:
                continue
            path_str = str(value).strip()
            candidate_name = path_str.split("/")[-1]
            lower = candidate_name.lower()
            if suffixes and not any(lower.endswith(suffix) for suffix in suffixes):
                continue
            if candidate_name not in discovered:
                discovered.append(candidate_name)
        return sorted(discovered)

    @staticmethod
    def _extract_row_count(df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        for field in ("rows", "row_count", "rows_affected", "affected_rows", "count"):
            if field in df.columns:
                try:
                    return int(df[field].iloc[0])
                except (KeyError, IndexError, ValueError, TypeError) as exc:
                    # Log and continue to try other common row count fields
                    logger.debug(f"Failed to extract row count from field '{field}': {exc}")
                    continue
        return len(df)

    def _build_definition(
        self,
        workspace: str,
        schema_name: str,
        table_name: str,
        columns: List[Dict[str, Any]],
        table_comment: Optional[str] = "",
        table_type: str = "table",
    ) -> str:
        column_lines: List[str] = []
        for column in columns:
            col_name = column.get("column_name") or column.get("name") or ""
            data_type = column.get("data_type") or column.get("type") or "STRING"
            comment = column.get("comment")
            col_def = f"`{_safe_escape_identifier(col_name)}` {data_type}"
            if comment:
                col_def += f" COMMENT '{_safe_escape(str(comment))}'"
            column_lines.append(col_def)

        columns_section = ",\n  ".join(column_lines) if column_lines else ""
        # Build table name parts
        escaped_workspace = _safe_escape_identifier(workspace)
        escaped_schema = _safe_escape_identifier(schema_name)
        escaped_table = _safe_escape_identifier(table_name)
        table_full_name = f"`{escaped_workspace}`.`{escaped_schema}`.`{escaped_table}`"

        definition = (
            f"CREATE {table_type.upper()} {table_full_name} (\n  {columns_section}\n)"
            if columns_section
            else f"CREATE {table_type.upper()} {table_full_name}"
        )
        if table_comment:
            definition += f"\nCOMMENT = '{_safe_escape(str(table_comment))}'"
        return definition

    # ------------------------------------------------------------------ #
    # BaseSqlConnector abstract methods
    # ------------------------------------------------------------------ #
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            df = self._run_command(sql)
            row_count = self._extract_row_count(df)
            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=str(row_count),
                row_count=row_count,
            )
        except DatusDbException as exc:
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql, sql_return="", row_count=0)

    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self.execute_insert(sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)

    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return self.execute_insert(sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)

    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            df = self._run_query(sql)
            row_count = len(df)
            if result_format == "csv":
                sql_return: Any = df.to_csv(index=False)
            elif result_format == "arrow":
                sql_return = pa.Table.from_pandas(df)
            elif result_format == "list":
                sql_return = df.to_dict(orient="records")
            else:
                sql_return = df

            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=sql_return,
                row_count=row_count,
                result_format=result_format,
            )
        except DatusDbException as exc:
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql)
        except Exception as exc:  # Ensure unexpected errors are also surfaced in result
            # Surface unexpected errors in logs while keeping graceful result contract
            logger.error(f"Unexpected error in execute_query: {exc}", exc_info=True)
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql)

    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        result = self.execute_query(sql, result_format="pandas")
        if not result.success:
            return result
        if hasattr(result.sql_return, "empty"):
            result.row_count = len(result.sql_return)
        return result

    def execute_query_to_df(self, sql: str, max_rows: Optional[int] = None) -> pd.DataFrame:
        """Execute query and directly return pandas DataFrame for convenience."""
        try:
            df = self._run_query(sql)
            if max_rows is not None and len(df) > max_rows:
                df = df.head(max_rows)
            return df
        except DatusDbException:
            raise
        except Exception as e:
            _log_sql_exception("ClickZetta DataFrame query failed", sql, e)
            raise DatusDbException(
                code=ErrorCode.DB_EXECUTION_ERROR,
                message=f"Failed to execute query to DataFrame: {str(e)}",
            ) from e

    def execute_query_to_dict(self, sql: str) -> List[Dict[str, Any]]:
        """Execute query and return result as list of dictionaries for JSON serialization."""
        try:
            df = self._run_query(sql)
            if df.empty:
                return []
            # Convert DataFrame to list of dictionaries with records orientation
            return df.to_dict(orient="records")
        except DatusDbException:
            raise
        except Exception as e:
            _log_sql_exception("ClickZetta dictionary query failed", sql, e)
            raise DatusDbException(
                code=ErrorCode.DB_EXECUTION_ERROR,
                message=f"Failed to execute query to dict: {str(e)}",
            ) from e

    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            self._run_command(sql)
            return ExecuteSQLResult(success=True, sql_query=sql, sql_return="Successful", row_count=0)
        except DatusDbException as exc:
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql)

    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        result = self.execute_query(sql, result_format="csv")
        return result

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        """Execute query and return result with Arrow data format for high performance."""
        try:
            df = self._run_query(sql)
            if df.empty:
                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=pa.Table.from_pandas(df),
                    row_count=0,
                    result_format="arrow",
                )

            # Convert pandas DataFrame to Arrow Table
            arrow_table = pa.Table.from_pandas(df)

            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=arrow_table,
                row_count=len(df),
                result_format="arrow",
            )
        except DatusDbException:
            raise
        except Exception as e:
            _log_sql_exception("ClickZetta Arrow query failed", sql, e)
            raise DatusDbException(
                code=ErrorCode.DB_EXECUTION_ERROR,
                message=f"Failed to execute Arrow query: {str(e)}",
            ) from e

    def test_connection(self):
        self._run_query("SELECT 1")

    def execute_queries(self, queries: List[str]) -> List[Any]:
        results: List[Any] = []
        for query in queries:
            if parse_sql_type(query, self.dialect) == SQLType.SELECT:
                df = self._run_query(query)
                results.append(df.to_dict(orient="records"))
            else:
                command_df = self._run_command(query)
                results.append(self._extract_row_count(command_df))
        return results

    def execute_queries_arrow(self, queries: List[str]) -> List[ExecuteSQLResult]:
        """Execute multiple queries and return results in Arrow format for batch processing performance."""
        results: List[ExecuteSQLResult] = []
        for query in queries:
            try:
                if parse_sql_type(query, self.dialect) == SQLType.SELECT:
                    # Use execute_arrow for SELECT queries to get Arrow data
                    result = self.execute_arrow(query)
                    results.append(result)
                else:
                    # For non-SELECT queries, use standard execution
                    command_df = self._run_command(query)
                    row_count = self._extract_row_count(command_df)
                    results.append(
                        ExecuteSQLResult(
                            success=True,
                            sql_return=None,
                            sql_query=query,
                            row_count=row_count,
                            result_format="arrow",
                        )
                    )
            except Exception as e:
                logger.error(f"Error executing query in batch: {query}, error: {str(e)}")
                # Add failed result to maintain query order
                results.append(
                    ExecuteSQLResult(
                        success=False,
                        sql_return=None,
                        sql_query=query,
                        row_count=0,
                        error=str(e),
                        result_format="arrow",
                    )
                )
        return results

    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        try:
            self._run_command(sql_query)
            switch_context = parse_context_switch(sql=sql_query, dialect=self.dialect)
            if switch_context:
                if catalog_name := switch_context.get("catalog_name"):
                    self.catalog_name = catalog_name
                if database_name := switch_context.get("database_name"):
                    self.database_name = database_name
                if schema_name := switch_context.get("schema_name"):
                    self.schema_name = schema_name
            return ExecuteSQLResult(success=True, sql_query=sql_query, sql_return="Successful", row_count=0)
        except DatusDbException as exc:
            return ExecuteSQLResult(success=False, error=str(exc), sql_query=sql_query)

    # ------------------------------------------------------------------ #
    # Metadata helpers
    # ------------------------------------------------------------------ #
    def _info_schema(self, workspace: Optional[str] = None) -> str:
        workspace = workspace or self.workspace
        # Quote workspace with backticks to handle special characters or case sensitivity
        escaped_workspace = _safe_escape_identifier(workspace)
        return f"`{escaped_workspace}`.information_schema"

    def _normalized_schema(self, schema_name: Optional[str] = None) -> str:
        schema = schema_name or self.schema_name
        return schema.upper() if schema else ""

    @property
    def workspace(self) -> str:
        return self.database_name or self._workspace

    def get_catalogs(self) -> List[str]:
        try:
            df = self._run_query("SHOW CATALOGS")
            if "catalog_name" in df.columns:
                return df["catalog_name"].dropna().tolist()
            if "name" in df.columns:
                return df["name"].dropna().tolist()
        except DatusDbException:
            logger.debug("SHOW CATALOGS not supported, returning workspace as catalog fallback")
        return [self.database_name] if self.database_name else []

    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        # In ClickZetta workspace ~= database concept
        if self.database_name:
            return [self.database_name]
        return []

    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        workspace = database_name or self.database_name
        if not workspace:
            return []
        info_schema = self._info_schema(workspace)
        sql = f"SELECT DISTINCT table_schema FROM {info_schema}.tables"
        try:
            df = self._run_query(sql)
            schemas = df["table_schema"].dropna().tolist()
            if not include_sys:
                schemas = [s for s in schemas if not str(s).startswith("INFORMATION_SCHEMA")]
            return schemas
        except DatusDbException:
            return [self.schema_name] if self.schema_name else []

    @staticmethod
    def _qualify(name, real_db, real_schema, arg_db, arg_schema):
        """Prefix the table with the db/schema levels the caller left blank.

        Yields ``[db.][schema.]table`` so an unscoped listing stays addressable; a level is
        prepended only when the caller passed it empty and the connector resolved that coordinate.
        """
        parts = []
        if not arg_db and real_db:
            parts.append(real_db)
        if not arg_schema and real_schema:
            parts.append(real_schema)
        parts.append(name)
        return ".".join(parts)

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        workspace = database_name or self.database_name
        schema = self._normalized_schema(schema_name)
        if not workspace or not schema:
            return []
        info_schema = self._info_schema(workspace)
        sql = (
            f"SELECT table_name, table_type FROM {info_schema}.tables "
            f"WHERE upper(table_schema) = '{_safe_escape(schema)}'"
        )
        df = self._run_query(sql)
        if df.empty:
            return []
        valid_types = {"MANAGED_TABLE", "EXTERNAL_TABLE", "BASE TABLE", "TABLE"}
        return [
            self._qualify(row.table_name, workspace, schema, database_name, schema_name)
            for row in df.itertuples()
            if str(row.table_type).upper() in valid_types
        ]

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        workspace = database_name or self.database_name
        schema = self._normalized_schema(schema_name)
        if not workspace or not schema:
            return []
        info_schema = self._info_schema(workspace)
        sql = (
            f"SELECT table_name, table_type FROM {info_schema}.tables "
            f"WHERE upper(table_schema) = '{_safe_escape(schema)}'"
        )
        try:
            df = self._run_query(sql)
            if df.empty:
                return []
            view_types = {"VIEW", "DYNAMIC_TABLE"}
            return [
                self._qualify(row.table_name, workspace, schema, database_name, schema_name)
                for row in df.itertuples()
                if str(row.table_type).upper() in view_types
            ]
        except DatusDbException:
            return []

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        workspace = database_name or self.database_name
        schema = self._normalized_schema(schema_name)
        if not workspace or not schema:
            return []
        info_schema = self._info_schema(workspace)
        sql = (
            f"SELECT table_name, table_type FROM {info_schema}.tables "
            f"WHERE upper(table_schema) = '{_safe_escape(schema)}'"
        )
        try:
            df = self._run_query(sql)
            if df.empty:
                return []
            return [
                self._qualify(row.table_name, workspace, schema, database_name, schema_name)
                for row in df.itertuples()
                if str(row.table_type).upper() == "MATERIALIZED_VIEW"
            ]
        except DatusDbException:
            return []

    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        return self._collect_table_definitions(
            database_name=database_name,
            schema_name=schema_name,
            tables=tables,
            include_views=False,
        )

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        return self._collect_table_definitions(
            database_name=database_name,
            schema_name=schema_name,
            include_views=True,
        )

    def _collect_table_definitions(
        self,
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
        include_views: bool = False,
    ) -> List[Dict[str, str]]:
        workspace = database_name or self.database_name
        schema = self._normalized_schema(schema_name)
        if not workspace or not schema:
            return []

        info_schema = self._info_schema(workspace)
        base_query = (
            f"SELECT table_name, comment, table_type "
            f"FROM {info_schema}.tables WHERE upper(table_schema) = '{_safe_escape(schema)}'"
        )
        if tables:
            table_list = ",".join(f"'{_safe_escape(tbl)}'" for tbl in tables)
            base_query += f" AND table_name IN ({table_list})"
        try:
            tables_df = self._run_query(base_query)
        except DatusDbException:
            return []

        column_query = (
            f"SELECT table_name, column_name, data_type, comment "
            f"FROM {info_schema}.columns WHERE upper(table_schema) = '{_safe_escape(schema)}'"
        )
        if tables:
            table_list = ",".join(f"'{_safe_escape(tbl)}'" for tbl in tables)
            column_query += f" AND table_name IN ({table_list})"
        column_query += " ORDER BY table_name, column_name"

        try:
            columns_df = self._run_query(column_query)
        except DatusDbException:
            columns_df = pd.DataFrame()

        columns_map: Dict[str, List[Dict[str, Any]]] = {}
        if not columns_df.empty:
            for item in columns_df.to_dict(orient="records"):
                table_name = item.get("table_name")
                if not table_name:
                    continue
                columns_map.setdefault(table_name, []).append(item)

        records: List[Dict[str, str]] = []
        for table_item in tables_df.to_dict(orient="records"):
            table_name = table_item.get("table_name")
            if not table_name:
                continue
            table_type_raw = str(table_item.get("table_type", "")).upper()
            is_view = table_type_raw in {"VIEW", "DYNAMIC_TABLE"}
            is_mv = table_type_raw == "MATERIALIZED_VIEW"
            if include_views and not is_view:
                continue
            if not include_views and (is_view or is_mv):
                continue
            table_type = "view" if is_view else "table"
            if is_mv:
                table_type = "mv"
            definition = self._build_definition(
                workspace=workspace,
                schema_name=schema,
                table_name=table_name,
                columns=columns_map.get(table_name, []),
                table_comment=table_item.get("comment"),
                table_type=table_type if table_type != "mv" else "table",
            )
            records.append(
                {
                    "identifier": metadata_identifier(
                        database_name=workspace,
                        schema_name=schema,
                        table_name=table_name,
                        dialect=self.dialect,
                    ),
                    "catalog_name": "",
                    "database_name": workspace,
                    "schema_name": schema,
                    "table_name": table_name,
                    "definition": definition,
                    "table_type": table_type,
                }
            )
        return records

    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        if not table_name:
            return []
        workspace = database_name or self.database_name
        schema = self._normalized_schema(schema_name)
        if not workspace or not schema:
            return []

        info_schema = self._info_schema(workspace)
        sql = (
            f"SELECT column_name, data_type, comment "
            f"FROM {info_schema}.columns "
            f"WHERE upper(table_schema) = '{_safe_escape(schema)}' "
            f"AND table_name = '{_safe_escape(table_name)}' "
            f"ORDER BY column_name"
        )
        df = self._run_query(sql)
        result: List[Dict[str, Any]] = []
        for idx, item in enumerate(df.to_dict(orient="records")):
            result.append(
                {
                    "cid": idx,
                    "name": item.get("column_name"),
                    "type": item.get("data_type"),
                    "comment": item.get("comment"),
                    "nullable": True,
                    "pk": False,
                    "default_value": None,
                }
            )
        return result

    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, Any]]:
        workspace = database_name or self.database_name
        schema = schema_name or self.schema_name
        if not workspace or not schema:
            return []

        tables_to_sample = tables or self.get_tables(database_name=workspace, schema_name=schema)
        samples: List[Dict[str, Any]] = []
        for table_name in tables_to_sample:
            # Build table name parts for sample query
            escaped_workspace = _safe_escape_identifier(workspace)
            escaped_schema = _safe_escape_identifier(schema)
            escaped_table = _safe_escape_identifier(table_name)
            table_full_name = f"`{escaped_workspace}`.`{escaped_schema}`.`{escaped_table}`"
            sql = f"SELECT * FROM {table_full_name} LIMIT {top_n}"
            try:
                df = self._run_query(sql)
            except DatusDbException:
                continue
            if df.empty:
                continue
            samples.append(
                {
                    "catalog_name": "",
                    "database_name": workspace,
                    "schema_name": schema,
                    "table_name": table_name,
                    "sample_rows": df.to_csv(index=False),
                }
            )
        return samples

    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        workspace = database_name or self.database_name
        schema = schema_name or self.schema_name
        if workspace and schema:
            return f"{workspace}.{schema}.{table_name}"
        if schema:
            return f"{schema}.{table_name}"
        return table_name

    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        db = database_name or self.database_name
        schema = schema_name or self.schema_name
        # For ClickZetta, prefer a clear 3-part identifier when possible
        if db and schema:
            return f"{db}.{schema}.{table_name}"
        return metadata_identifier(
            catalog_name=catalog_name,
            database_name=db,
            schema_name=schema,
            table_name=table_name,
            dialect=self.dialect,
        )

    def execute(self, input_params: Any, result_format: Optional[str] = None) -> ExecuteSQLResult:
        """Execute a SQL query against the database.

        This method provides compatibility with the Datus CLI interface for SQL execution.

        Args:
            input_params: Dictionary containing input parameters including sql_query,
                         or ExecuteSQLInput object, or string SQL query
            result_format: The format of the result to return ("csv", "arrow", "pandas", "list")

        Returns:
            ExecuteSQLResult containing the query results
        """
        # Handle different input types
        if isinstance(input_params, str):
            sql_query = input_params
        elif hasattr(input_params, "sql_query"):
            sql_query = input_params.sql_query
        elif isinstance(input_params, dict):
            sql_query = input_params.get("sql_query", "")
        else:
            raise DatusDbException(
                ErrorCode.COMMON_INVALID_PARAMETER,
                message=f"Invalid input_params type: {type(input_params)}",
            )

        if not sql_query:
            raise DatusDbException(ErrorCode.COMMON_INVALID_PARAMETER, message="sql_query cannot be empty")

        # Resolve result_format from input_params if not explicitly provided
        if result_format is None:
            if hasattr(input_params, "result_format"):
                result_format = input_params.result_format
            elif isinstance(input_params, dict):
                result_format = input_params.get("result_format", "csv")
            else:
                result_format = "csv"

        # Route by SQL type first, then by result_format for queries
        try:
            sql_query = sql_query.strip()
            sql_type = parse_sql_type(sql_query, self.dialect)
            if sql_type == SQLType.INSERT:
                return self.execute_insert(sql_query)
            elif sql_type in (SQLType.UPDATE, SQLType.MERGE):
                return self.execute_update(sql_query)
            elif sql_type == SQLType.DELETE:
                return self.execute_delete(sql_query)
            elif sql_type == SQLType.CONTENT_SET:
                return self.execute_content_set(sql_query)
            elif sql_type == SQLType.DDL:
                return self.execute_ddl(sql_query)
            elif sql_type in (SQLType.SELECT, SQLType.METADATA_SHOW, SQLType.EXPLAIN):
                return self.execute_query(sql_query, result_format)
            else:
                return ExecuteSQLResult(
                    success=False,
                    error="Unknown type of SQL",
                    sql_query=sql_query,
                    sql_return="",
                    row_count=0,
                    result_format=result_format,
                )
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

    def __len__(self) -> int:
        """Return a length value for compatibility with system expectations.

        Some parts of the system may expect connector objects to have a length.
        We return 1 to indicate the connector is present and functional.
        """
        return 1

    def values(self):
        """Return an iterable of values for compatibility with dict-like expectations.

        Some parts of the system may expect connector objects to be dict-like.
        We return a list containing just this connector.
        """
        return [self]

    def items(self):
        """Return an iterable of key-value pairs for dict-like compatibility."""
        return [("clickzetta", self)]

    def keys(self):
        """Return an iterable of keys for dict-like compatibility."""
        return ["clickzetta"]
