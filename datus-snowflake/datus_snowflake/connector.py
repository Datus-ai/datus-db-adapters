# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Union, override

import pyarrow as pa
import pyarrow.compute as pc
from cryptography.hazmat.primitives import serialization
from pandas import DataFrame
from snowflake.connector import Connect, SnowflakeConnection
from snowflake.connector.errors import (
    DatabaseError,
    DataError,
    ForbiddenError,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    RequestTimeoutError,
    ServiceUnavailableError,
)

from datus_db_core import (
    TABLE_TYPE,
    BaseSqlConnector,
    ConnectionConfig,
    DatusDbException,
    ErrorCode,
    ExecuteSQLResult,
    MaterializedViewSupportMixin,
    MigrationTargetMixin,
    SchemaNamespaceMixin,
    get_logger,
    list_to_in_str,
    parse_context_switch,
    to_sql_literal,
)

from .config import SnowflakeConfig

logger = get_logger(__name__)

_SQL_PREVIEW_CHARS = 50


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


def _private_key_to_der(private_key: str, private_key_file_pwd: Optional[str] = None) -> bytes:
    """Convert a PEM private key string into DER bytes accepted by Snowflake."""
    normalized_pem = private_key.strip()
    if "\\n" in normalized_pem and "\n" not in normalized_pem:
        normalized_pem = normalized_pem.replace("\\n", "\n")

    passphrase = private_key_file_pwd.encode() if private_key_file_pwd else None
    try:
        private_key = serialization.load_pem_private_key(normalized_pem.encode(), password=passphrase)
    except TypeError:
        if passphrase is None:
            raise
        private_key = serialization.load_pem_private_key(normalized_pem.encode(), password=None)
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _handle_snowflake_exception(e: Exception, sql: str = "") -> DatusDbException:
    """Handle Snowflake exceptions and map to appropriate Datus ErrorCode."""
    error_message = getattr(e, "raw_msg", None) or getattr(e, "msg", None) or str(e)
    _log_sql_exception("Snowflake SQL execution failed", sql, e)

    if isinstance(e, ProgrammingError):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_SYNTAX_ERROR,
            message_args={"sql": sql, "error_message": error_message},
        )

    elif isinstance(e, (OperationalError, DatabaseError)):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_ERROR,
            message_args={"sql": sql, "error_message": error_message},
        )

    elif isinstance(e, IntegrityError):
        return DatusDbException(
            ErrorCode.DB_CONSTRAINT_VIOLATION,
            message_args={"sql": sql, "error_message": error_message},
        )

    elif isinstance(e, (RequestTimeoutError, ServiceUnavailableError)):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_TIMEOUT,
            message_args={"sql": sql, "error_message": error_message},
        )

    elif isinstance(e, (InterfaceError, InternalError)):
        return DatusDbException(ErrorCode.DB_CONNECTION_FAILED, message_args={"error_message": error_message})

    elif isinstance(e, ForbiddenError):
        return DatusDbException(
            ErrorCode.DB_PERMISSION_DENIED,
            message_args={"operation": "query execution", "error_message": error_message},
        )

    elif isinstance(e, (DataError, NotSupportedError)):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_ERROR,
            message_args={"sql": sql, "error_message": error_message},
        )

    else:
        return DatusDbException(ErrorCode.DB_FAILED, message_args={"error_message": error_message})


class SnowflakeConnector(BaseSqlConnector, SchemaNamespaceMixin, MaterializedViewSupportMixin, MigrationTargetMixin):
    """
    Connector for Snowflake databases using native Snowflake SDK.

    This connector provides full support for Snowflake features including:
    - Multi-database and schema support
    - Tables, views, and materialized views
    - Efficient metadata retrieval using SHOW commands
    - Arrow-based query execution for performance

    Implements SchemaNamespaceMixin and MaterializedViewSupportMixin.
    """

    def __init__(self, config: Union[SnowflakeConfig, dict]):
        """
        Initialize Snowflake connector.

        Args:
            config: SnowflakeConfig object or dict with configuration
        """
        # Handle config object or dict
        if isinstance(config, dict):
            config = SnowflakeConfig(**config)
        elif not isinstance(config, SnowflakeConfig):
            raise TypeError(f"config must be SnowflakeConfig or dict, got {type(config)}")

        self.snowflake_config = config

        conn_config = ConnectionConfig(timeout_seconds=config.timeout_seconds)
        super().__init__(config=conn_config, dialect="snowflake")

        connect_kwargs: Dict[str, Any] = {
            "account": config.account,
            "user": config.username,
            "warehouse": config.warehouse,
            "database": config.database if config.database else None,
            "schema": config.schema_name if config.schema_name else None,
            "login_timeout": config.timeout_seconds,
            "network_timeout": config.timeout_seconds,
            "socket_timeout": config.timeout_seconds,
        }
        if config.role:
            connect_kwargs["role"] = config.role
        if config.private_key and config.private_key.get_secret_value():
            connect_kwargs["authenticator"] = "SNOWFLAKE_JWT"
            private_key_file_pwd = (
                config.private_key_file_pwd.get_secret_value() if config.private_key_file_pwd else None
            )
            connect_kwargs["private_key"] = _private_key_to_der(
                config.private_key.get_secret_value(),
                private_key_file_pwd,
            )
        elif config.private_key_file:
            connect_kwargs["authenticator"] = "SNOWFLAKE_JWT"
            connect_kwargs["private_key_file"] = config.private_key_file
            if config.private_key_file_pwd:
                connect_kwargs["private_key_file_pwd"] = config.private_key_file_pwd.get_secret_value()
        else:
            connect_kwargs["password"] = config.password.get_secret_value()

        self.connection: SnowflakeConnection = Connect(**connect_kwargs)
        self.database_name = config.database or ""
        self.schema_name = config.schema_name or ""

    def test_connection(self) -> Dict[str, Any]:
        """Test the database connection."""
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchall()
            return {
                "success": True,
                "message": "Connection successful",
                "databases": "",
            }

    def close(self):
        """Close the database connection."""
        self.connection.close()

    def get_type(self) -> str:
        """Return the database type."""
        return "snowflake"

    def _sys_databases(self) -> Set[str]:
        """Return set of system databases to filter out."""
        return {"SNOWFLAKE"}

    def _sys_schemas(self) -> Set[str]:
        """Return set of system schemas to filter out."""
        return {"INFORMATION_SCHEMA"}

    def _reject_catalog(self, catalog_name: str = "") -> None:
        if catalog_name:
            raise ValueError("Snowflake does not support a catalog namespace; use database_name instead.")

    def do_switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Switch database or schema context."""
        self._reject_catalog(catalog_name)
        try:
            with self.connection.cursor() as cursor:
                if not schema_name:
                    if not database_name:
                        return
                    sql = f'USE DATABASE "{database_name}"'
                else:
                    sql = (
                        f'USE SCHEMA "{schema_name}"' if not database_name else f'USE "{database_name}"."{schema_name}"'
                    )
                cursor.execute(sql)
        except Exception as e:
            raise _handle_snowflake_exception(e, sql) from e

    def validate_input(self, input_params: Dict[str, Any]):
        """Validate input parameters."""
        super().validate_input(input_params)
        if "params" in input_params:
            if not isinstance(input_params["params"], Sequence) and not isinstance(input_params["params"], dict):
                raise ValueError("params must be dict or Sequence")

    def _do_execute_arrow(
        self, sql_query: str, params: Optional[Sequence[Any] | dict[Any, Any]] = None
    ) -> tuple[pa.Table, int]:
        """Execute SQL query and return results in Apache Arrow format."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute("ALTER SESSION SET PYTHON_CONNECTOR_QUERY_RESULT_FORMAT='ARROW'")
                cursor.execute(sql_query, params)
                try:
                    arrow_table = cursor.fetch_arrow_all(force_return_table=True)
                except NotSupportedError:
                    # Snowflake can return JSON for statements such as EXPLAIN
                    # even when Arrow is requested for the session. In that case
                    # fetch_arrow_all() rejects the result before consuming it,
                    # so fetch the rows normally and materialize the requested
                    # Arrow result locally.
                    rows = cursor.fetchall()
                    column_names = [getattr(column, "name", None) or column[0] for column in (cursor.description or [])]
                    arrays = [
                        pa.array([row.get(column_name) if isinstance(row, dict) else row[index] for row in rows])
                        for index, column_name in enumerate(column_names)
                    ]
                    arrow_table = pa.Table.from_arrays(arrays, names=column_names)
                return arrow_table, cursor.rowcount
        except Exception as e:
            raise _handle_snowflake_exception(e, sql_query)

    def execute_query_to_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[Any, Any] | None = None,
    ) -> DataFrame:
        """Execute query and return pandas DataFrame."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetch_pandas_all()
        except Exception as e:
            raise _handle_snowflake_exception(e, sql) from e

    def execute_query_to_dict(self, sql: str) -> List[Dict[str, Any]]:
        """Execute query and return list of dictionaries."""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                query_result = cursor.fetchall()
                if not query_result or isinstance(query_result[0], dict):
                    return query_result
                result = []
                for item in query_result:
                    item_dict = {}
                    for i, col in enumerate(cursor.description):
                        item_dict[col.name] = item[i]
                    result.append(item_dict)
            return result
        except Exception as e:
            raise _handle_snowflake_exception(e, sql) from e

    @override
    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute DDL statement."""
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    @override
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute INSERT statement."""
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                rowcount = cursor.rowcount
                last_rowid = cursor.sfqid

                return ExecuteSQLResult(
                    sql_query=sql,
                    row_count=rowcount,
                    sql_return=str(last_rowid),
                    success=True,
                    error=None,
                )
        except Exception as e:
            ex = _handle_snowflake_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                sql_query=sql,
                error=str(ex),
            )

    @override
    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute UPDATE statement."""
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    @override
    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute DELETE statement."""
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    def _execute_update_or_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute UPDATE or DELETE statement."""
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                rowcount = cursor.rowcount

                return ExecuteSQLResult(
                    sql_query=sql,
                    row_count=rowcount,
                    sql_return=str(rowcount),
                    success=True,
                    error=None,
                )
        except Exception as e:
            ex = _handle_snowflake_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                sql_query=sql,
                error=str(ex),
            )

    @override
    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        """Execute context switch statement (USE DATABASE/SCHEMA)."""
        try:
            switch_context = parse_context_switch(sql=sql_query, dialect=self.dialect)
            if switch_context:
                if catalog_name := switch_context.get("catalog_name"):
                    self._reject_catalog(catalog_name)
            with self.connection.cursor() as cursor:
                cursor.execute(sql_query)
            if switch_context:
                if database_name := switch_context.get("database_name"):
                    self.database_name = database_name
                if schema_name := switch_context.get("schema_name"):
                    self.schema_name = schema_name
            return ExecuteSQLResult(
                success=True,
                sql_query=sql_query,
                sql_return="Successful",
                row_count=0,
            )
        except Exception as e:
            ex = _handle_snowflake_exception(e, sql_query)
            return ExecuteSQLResult(success=False, sql_query=sql_query, error=str(ex))

    @override
    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        """Execute query and return results in specified format."""
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        if sql.lower().startswith("show"):
            return self._execute_show(sql, result_format)

        if result_format == "csv":
            return self.execute_csv(sql)
        elif result_format == "pandas":
            return self.execute_pandas(sql)
        else:
            result = self.execute_arrow(sql)
            if result_format == "arrow":
                return result
            if result and result.success:
                result.sql_return = result.sql_return.to_pylist()
                result.result_format = result_format
            return result

    def _execute_show(
        self, sql: str, result_format: Literal["csv", "arrow", "pandas", "list"] = "csv"
    ) -> ExecuteSQLResult:
        """Execute SHOW command with special handling."""
        sql = sql.strip()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                result = cursor.fetchall()
                col_names = [col[0] for col in cursor.description][:7]
                row_count = len(result)

                if result:
                    columns = list(zip(*[row[:7] for row in result]))
                    arrow_result = pa.Table.from_arrays([pa.array(col) for col in columns], names=col_names)
                else:
                    arrow_result = pa.Table.from_arrays([])

                if result_format == "arrow":
                    final_result = arrow_result
                elif result_format == "list":
                    final_result = arrow_result.to_pylist()
                else:
                    df = arrow_result.to_pandas()
                    final_result = df if result_format == "pandas" else df.to_csv(index=False)

                return ExecuteSQLResult(
                    success=True,
                    result_format=result_format,
                    sql_return=final_result,
                    row_count=row_count,
                )
        except Exception as e:
            ex = _handle_snowflake_exception(e, sql)
            return ExecuteSQLResult(success=False, sql_query=sql, result_format=result_format, error=str(ex))

    def execute_arrow(self, sql: str) -> ExecuteSQLResult:
        """Execute query and return Arrow table."""
        try:
            arrow_table, row_count = self._do_execute_arrow(sql)
            if arrow_table is None:
                logger.debug(f"Arrow table is None for query. Row count: {row_count}")
                row_count = 0
                arrow_table = None
            else:
                row_count = arrow_table.num_rows

            return ExecuteSQLResult(
                sql_query=sql,
                row_count=row_count,
                sql_return=arrow_table,
                success=True,
                error=None,
                result_format="arrow",
            )
        except DatusDbException as e:
            return ExecuteSQLResult(success=False, sql_query=sql, error=str(e))

    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        """Execute query and return pandas DataFrame."""
        try:
            df = self.execute_query_to_df(sql)
            return ExecuteSQLResult(
                sql_query=sql,
                row_count=len(df),
                sql_return=df,
                success=True,
                error=None,
                result_format="pandas",
            )
        except DatusDbException as e:
            return ExecuteSQLResult(success=False, sql_query=sql, result_format="pandas", error=str(e))
        except Exception as e:
            ex = _handle_snowflake_exception(e, sql)
            return ExecuteSQLResult(success=False, sql_query=sql, result_format="pandas", error=str(ex))

    def execute_csv(self, query: str) -> ExecuteSQLResult:
        """Execute query and return CSV string."""
        result = self.execute_pandas(query)
        result.result_format = "csv"
        if result.success and result.row_count > 0:
            result.sql_return = result.sql_return.to_csv(index=False)
        return result

    def execute_queries(self, queries: List[str]) -> List[ExecuteSQLResult]:
        """Execute multiple queries."""
        return [self.execute_query(sql) for sql in queries]

    def execute_queries_arrow(self, queries: List[str]) -> List[ExecuteSQLResult]:
        """Execute multiple queries and return Arrow results."""
        return [self.execute_arrow(sql) for sql in queries]

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of databases."""
        self._reject_catalog(catalog_name)
        res = self._execute_show(sql="SHOW DATABASES", result_format="arrow").sql_return
        databases = res["name"]

        if not include_sys:
            system_dbs = pa.array(self._sys_databases(), type=pa.string())
            databases = databases.filter(pc.invert(pc.is_in(databases, system_dbs)))
            databases = [db.as_py() for db in databases if db.as_py().upper() not in self._sys_databases()]
        else:
            databases = databases.to_pylist()

        return databases

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of schemas using SHOW SCHEMAS command."""
        self._reject_catalog(catalog_name)
        database_name = database_name or self.database_name

        if database_name:
            sql = f'SHOW SCHEMAS IN DATABASE "{database_name}"'
        else:
            sql = "SHOW SCHEMAS"

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                results = cursor.fetchall()

                schemas = []
                for row in results:
                    schema_name = row[1]
                    if include_sys or schema_name.upper() not in self._sys_schemas():
                        schemas.append(schema_name)

                return schemas
        except Exception as e:
            logger.warning(f"Failed to get schemas using SHOW SCHEMAS, falling back to INFORMATION_SCHEMA: {e}")

            # Fallback to INFORMATION_SCHEMA
            select_table_name = (
                "INFORMATION_SCHEMA.SCHEMATA" if not database_name else f'"{database_name}".INFORMATION_SCHEMA.SCHEMATA'
            )

            sql = f"SELECT SCHEMA_NAME FROM {select_table_name}"
            if not include_sys:
                sql += " WHERE SCHEMA_NAME NOT IN ('INFORMATION_SCHEMA')"

            if database_name:
                if not include_sys:
                    sql += f" AND CATALOG_NAME='{database_name}'"
                else:
                    sql += f" WHERE CATALOG_NAME='{database_name}'"

            try:
                df = self.execute_query_to_df(sql=sql)
                return [item for item in df["SCHEMA_NAME"]]
            except Exception as e:
                raise _handle_snowflake_exception(e, sql) from e

    @override
    @staticmethod
    def _qualify_name(meta, arg_db, arg_schema):
        """Prefix the table with the db/schema levels the caller left blank.

        Yields ``[db.][schema.]table`` so an unscoped listing stays addressable; a level is
        prepended only when the caller passed it empty and the row carries that coordinate.
        """
        parts = []
        if not arg_db and meta.get("database_name"):
            parts.append(meta["database_name"])
        if not arg_schema and meta.get("schema_name"):
            parts.append(meta["schema_name"])
        parts.append(meta["table_name"])
        return ".".join(parts)

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of table names."""
        self._reject_catalog(catalog_name)
        tables = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="table",
        )
        return [self._qualify_name(item, database_name, schema_name) for item in tables]

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of view names."""
        self._reject_catalog(catalog_name)
        views = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="view",
        )
        return [self._qualify_name(view, database_name, schema_name) for view in views]

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """Get list of materialized view names."""
        self._reject_catalog(catalog_name)
        mvs = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="mv",
        )
        return [self._qualify_name(mv, database_name, schema_name) for mv in mvs]

    def _get_tables_per_db(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
        table_type: TABLE_TYPE = "",
    ) -> List[Dict[str, str]]:
        """Get table metadata (excluding DDL)."""
        self._reject_catalog(catalog_name)
        catalog_name = ""
        database_name = database_name or self.database_name
        result = []

        if not database_name:
            dbs = self.get_databases(catalog_name=catalog_name)
            for db in dbs:
                self._get_tables_single_db(
                    result=result,
                    database_name=db,
                    catalog_name=catalog_name,
                    schema_name=schema_name,
                    tables=tables,
                    table_type=table_type,
                )
        else:
            self._get_tables_single_db(
                result=result,
                database_name=database_name,
                catalog_name=catalog_name,
                schema_name=schema_name,
                tables=tables,
                table_type=table_type,
            )
        return result

    def _get_tables_single_db(
        self,
        result: List[Dict[str, Any]],
        database_name: str,
        catalog_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
        table_type: TABLE_TYPE = "",
    ):
        """Get tables from a single database."""
        if table_type in ("table", "full"):
            db_tables = self._do_get_metas(
                database_name=database_name,
                schema_name=schema_name,
                tables=tables,
                meta_name="TABLES",
            )
            result.extend(self._metadata_to_dict(db_tables, "table", catalog_name))

        if table_type in ("view", "full"):
            db_views = self._do_get_metas(
                database_name=database_name,
                schema_name=schema_name,
                tables=tables,
                meta_name="VIEWS",
            )
            result.extend(self._metadata_to_dict(db_views, "view", catalog_name))

        if table_type in ("mv", "full"):
            db_mvs = self._do_get_metas(
                database_name=database_name,
                schema_name=schema_name,
                tables=tables,
                meta_name="MATERIALIZED VIEWS",
            )
            result.extend(self._metadata_to_dict(db_mvs, "mv", catalog_name))

    def _do_get_metas(
        self,
        database_name: str,
        schema_name: str = "",
        tables: Optional[List[str]] = None,
        meta_name: str = "TABLES",
    ) -> pa.Table:
        """Get metadata using SHOW command."""
        meta_name = meta_name.upper()
        sql = f'SHOW TERSE {meta_name} IN DATABASE "{database_name}"'

        try:
            query_tables = self.execute_query_to_dict(sql)
            if not query_tables:
                return pa.table([])

            query_tables = pa.Table.from_pylist(query_tables)

            if schema_name:
                query_tables = query_tables.filter(pc.equal(query_tables["schema_name"], schema_name))
            else:
                query_tables = query_tables.filter(
                    pc.invert(
                        pc.is_in(
                            query_tables["schema_name"],
                            pa.array(self._sys_schemas(), type=pa.string()),
                        )
                    )
                )

            if tables:
                query_tables = query_tables.filter(pc.is_in(query_tables["name"], pa.array(tables, type=pa.string())))

            return query_tables
        except Exception as e:
            logger.warning(f"Failed to get meta using {sql}, falling back to INFORMATION_SCHEMA: {e}")

            # Fallback to INFORMATION_SCHEMA
            select_table_name = f'"{database_name}".INFORMATION_SCHEMA.TABLES'

            if meta_name == "TABLES":
                table_type = "BASE TABLE"
            elif meta_name == "VIEWS":
                table_type = "VIEW"
            else:
                table_type = "MATERIALIZED VIEW"

            sql = f"""SELECT TABLE_CATALOG as "database_name", TABLE_SCHEMA as "schema_name", TABLE_NAME as "name"
            FROM {select_table_name} WHERE TABLE_TYPE = '{table_type}'"""

            if schema_name:
                sql += f" AND TABLE_SCHEMA = {to_sql_literal(schema_name, True)}"
            if tables:
                sql += list_to_in_str(prefix=" AND TABLE_NAME IN ", values=tables)

            try:
                tables_result, _ = self._do_execute_arrow(sql)
                return tables_result
            except Exception as e:
                raise _handle_snowflake_exception(e, sql) from e

    def _metadata_to_dict(
        self, tables: pa.Table, table_type: TABLE_TYPE, catalog_name: str = ""
    ) -> List[Dict[str, str]]:
        """Convert Arrow table metadata to dictionary list."""
        result = []
        for i in range(len(tables)):
            current_schema = tables["schema_name"][i].as_py()
            current_table_name = tables["name"][i].as_py()
            db_name = tables["database_name"][i].as_py()
            result.append(
                {
                    "catalog_name": catalog_name,
                    "database_name": db_name,
                    "schema_name": current_schema,
                    "table_name": current_table_name,
                    "table_type": table_type,
                    "identifier": self.identifier(
                        catalog_name=catalog_name,
                        database_name=db_name,
                        schema_name=current_schema,
                        table_name=current_table_name,
                    ),
                }
            )
        return result

    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
        table_type: str = "table",
    ) -> List[Dict[str, Any]]:
        """Get schema information for a table/view/materialized view."""
        if not table_name:
            return []

        self._reject_catalog(catalog_name)
        catalog_name = ""
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name

        full_name = self.full_name(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
        )
        table_type = table_type.upper()

        describe_target = {
            "TABLE": "TABLE",
            "VIEW": "VIEW",
            "MATERIALIZED VIEW": "MATERIALIZED VIEW",
            "MATERIALIZED_VIEW": "MATERIALIZED VIEW",
            "MV": "MATERIALIZED VIEW",
        }.get(table_type, "TABLE")

        describe_sql = f"DESCRIBE {describe_target} {full_name}"

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(describe_sql)
                describe_results = cursor.fetchall()
                column_names = [col[0].lower() for col in cursor.description]
        except Exception as e:
            raise _handle_snowflake_exception(e, describe_sql) from e

        def _row_map(row: Sequence[Any]) -> Dict[str, Any]:
            return {column_names[idx]: row[idx] for idx in range(min(len(column_names), len(row)))}

        schemas: List[Dict[str, Any]] = []
        columns_list: List[Dict[str, Any]] = []
        column_index = 0

        for row in describe_results:
            row_info = _row_map(row)
            kind = (row_info.get("kind") or "COLUMN").upper()
            if "COLUMN" not in kind:
                continue

            column_name = row_info.get("name")
            if not column_name:
                continue

            data_type = row_info.get("type", "")
            nullable_flag = str(row_info.get("null?") or row_info.get("null? ") or "").upper()
            default_value = row_info.get("default")
            comment = row_info.get("comment")
            pk_flag = str(row_info.get("primary key") or "").upper()

            column_info = {
                "cid": column_index,
                "name": column_name,
                "type": data_type,
                "nullable": nullable_flag == "Y",
                "pk": pk_flag == "Y" and table_type == "TABLE",
                "default_value": default_value,
                "comment": comment,
            }

            schemas.append(column_info)
            columns_list.append({"name": column_name, "type": data_type})
            column_index += 1

        schemas.append(
            {
                "table": table_name,
                "columns": columns_list,
                "table_type": table_type.lower(),
            }
        )

        return schemas

    def _fetch_object_ddl(self, object_type: str, full_name: str) -> str:
        """Retrieve DDL for a database object."""
        with self.connection.cursor() as cursor:
            sql = f"SELECT GET_DDL('{object_type}', '{full_name}', true)"
            try:
                cursor.execute(sql)
                row = cursor.fetchone()
                ddl = row[0] if row else ""
            except Exception as e:
                logger.warning(f"Failed to get DDL with {sql}: {e}")
                ddl = f"-- DDL not available for {object_type.lower()} {full_name}: {e}"
        return ddl

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Get table metadata with DDL definitions."""
        self._reject_catalog(catalog_name)
        table_entries = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            tables=tables,
            table_type="table",
        )

        if not table_entries:
            return []

        for entry in table_entries:
            ddl_name = self.full_name(
                database_name=entry["database_name"],
                schema_name=entry["schema_name"],
                table_name=entry["table_name"],
            )
            entry["definition"] = self._fetch_object_ddl("TABLE", ddl_name)

        return table_entries

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """Get view metadata with DDL definitions."""
        self._reject_catalog(catalog_name)
        view_entries = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="view",
        )

        if not view_entries:
            return []

        for entry in view_entries:
            ddl_name = self.full_name(
                database_name=entry["database_name"],
                schema_name=entry["schema_name"],
                table_name=entry["table_name"],
            )
            entry["definition"] = self._fetch_object_ddl("VIEW", ddl_name)

        return view_entries

    def get_materialized_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """Get materialized view metadata with DDL definitions."""
        self._reject_catalog(catalog_name)
        mv_entries = self._get_tables_per_db(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="mv",
        )

        if not mv_entries:
            return []

        for entry in mv_entries:
            ddl_name = self.full_name(
                database_name=entry["database_name"],
                schema_name=entry["schema_name"],
                table_name=entry["table_name"],
            )
            entry["definition"] = self._fetch_object_ddl("MATERIALIZED VIEW", ddl_name)

        return mv_entries

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
        """Get sample rows from tables."""
        result = []
        self._reject_catalog(catalog_name)
        catalog_name = ""
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name

        with self.connection.cursor() as cursor:
            if tables:
                for table in tables:
                    full_name = self.full_name(
                        catalog_name=catalog_name,
                        database_name=database_name,
                        schema_name=schema_name,
                        table_name=table,
                    )
                    sql = f"SELECT * FROM {full_name} LIMIT {top_n}"
                    res = cursor.execute(sql).fetch_pandas_all()
                    if not res.empty:
                        result.append(
                            {
                                "identifier": self.identifier(
                                    catalog_name=catalog_name,
                                    database_name=database_name,
                                    schema_name=schema_name,
                                    table_name=table,
                                ),
                                "catalog_name": catalog_name,
                                "database_name": database_name,
                                "schema_name": schema_name,
                                "table_name": table,
                                "table_type": table_type,
                                "sample_rows": res.to_csv(index=False),
                            }
                        )
            else:
                for t in self._get_tables_per_db(
                    catalog_name=catalog_name,
                    database_name=database_name,
                    schema_name=schema_name,
                    table_type=table_type,
                ):
                    full_table_name = self.full_name(
                        t["catalog_name"],
                        t["database_name"],
                        t["schema_name"],
                        t["table_name"],
                    )
                    sql = f"SELECT * FROM {full_table_name} LIMIT {top_n}"
                    res = cursor.execute(sql).fetch_pandas_all()
                    if not res.empty:
                        result.append(
                            {
                                "identifier": t["identifier"],
                                "catalog_name": t["catalog_name"],
                                "database_name": t["database_name"],
                                "schema_name": t["schema_name"],
                                "table_name": t["table_name"],
                                "table_type": t["table_type"],
                                "sample_rows": res.to_csv(index=False),
                            }
                        )
        return result

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Build fully qualified table name."""
        self._reject_catalog(catalog_name)
        if schema_name:
            full_name = f'"{schema_name}"."{table_name}"'
        else:
            full_name = f'"{table_name}"'
        return full_name if not database_name else f'"{database_name}".{full_name}'

    # ==================== MigrationTargetMixin ====================

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "snowflake",
            "requires": [],  # Snowflake handles partitioning transparently (micro-partitions)
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks-only)",
                "ENGINE = MergeTree (ClickHouse-only)",
            ],
            "type_hints": {
                "unbounded VARCHAR": "VARCHAR (up to 16MB; length often omitted)",
                "TEXT": "VARCHAR or TEXT (aliases)",
                "JSON": "VARIANT (preferred for semi-structured data)",
                "JSONB": "VARIANT",
                "MAP": "OBJECT",
                "ARRAY": "ARRAY",
                "HUGEINT": "NUMBER(38,0)",
                "LARGEINT": "NUMBER(38,0)",
                "DECIMAL(p,s)": "NUMBER(p,s)",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMP_TZ",
                "TIMESTAMP": "TIMESTAMP_NTZ",
                "BOOLEAN": "BOOLEAN",
                "UUID": "VARCHAR(36)",
                "clustering": "Optional WITH CLUSTER BY (<cols>) for large tables queried by date/tenant",
            },
            "example_ddl": (
                "CREATE TABLE db.schema.t (\n"
                "  id NUMBER(38,0),\n"
                "  event_date DATE,\n"
                "  payload VARIANT,\n"
                "  created_at TIMESTAMP_NTZ\n"
                ")\n"
                "CLUSTER BY (event_date)"
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Suggest optional CLUSTER BY on date/timestamp columns for larger tables."""
        if not columns:
            return {}
        import re as _re

        for col in columns:
            base_type = _re.sub(r"\(.*\)", "", str(col.get("type", "")).upper()).strip()
            if base_type in ("DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_TZ", "TIMESTAMP_LTZ"):
                return {"cluster_by": [col["name"]]}
        # No date column — defer to LLM / user judgment
        return {}

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()
        if "DUPLICATE KEY" in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; Snowflake uses CLUSTER BY")
        if "DISTRIBUTED BY" in upper:
            errors.append("DISTRIBUTED BY is StarRocks/Greenplum syntax; Snowflake uses CLUSTER BY")
        if "ENGINE =" in upper or "ENGINE=" in upper:
            errors.append("ENGINE clause is MySQL/ClickHouse syntax; not supported by Snowflake")
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "NUMBER(38,0)",
            "LARGEINT": "NUMBER(38,0)",
            "JSON": "VARIANT",
            "JSONB": "VARIANT",
            "MAP": "OBJECT",
            "STRUCT": "OBJECT",
            "DATETIME": "TIMESTAMP_NTZ",
            "TIMESTAMPTZ": "TIMESTAMP_TZ",
        }
        return overrides.get(base)
