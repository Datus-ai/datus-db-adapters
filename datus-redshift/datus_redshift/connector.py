# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

# Standard library imports
import re

# Import type hints for better code documentation and IDE support
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Union, override

# PyArrow is used for efficient data handling (columnar format)
import pyarrow as pa

# Import Redshift connector library
import redshift_connector

# Pandas is used for DataFrame operations
from pandas import DataFrame
from redshift_connector.error import (
    DatabaseError,
    DataError,
    IntegrityError,
    InterfaceError,
    InternalError,
    OperationalError,
    ProgrammingError,
)

# Import Datus base classes and types
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
    parse_context_switch,
)

# Import our config class
from .config import RedshiftConfig

# Get a logger instance for this module (for debugging and error messages)
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


def _handle_redshift_exception(e: Exception, sql: str = "") -> DatusDbException:
    """
    Handle Redshift exceptions and map them to appropriate Datus ErrorCode.

    This function takes a Redshift-specific exception and converts it into
    a standardized DatusDbException with appropriate error codes and messages.

    Args:
        e: The exception raised by Redshift
        sql: The SQL query that caused the exception (for error messages)

    Returns:
        DatusDbException with appropriate error code and message
    """
    _log_sql_exception("Redshift SQL execution failed", sql, e)

    # Check subclasses before parent classes to ensure correct error mapping.
    # IntegrityError, InternalError, DataError are all subclasses of DatabaseError,
    # so they must be checked first.

    # ProgrammingError = syntax errors, invalid SQL statements
    if isinstance(e, ProgrammingError):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_SYNTAX_ERROR,
            message_args={"sql": sql, "error_message": str(e)},
        )

    # IntegrityError = constraint violations (unique key, foreign key, etc.)
    elif isinstance(e, IntegrityError):
        return DatusDbException(
            ErrorCode.DB_CONSTRAINT_VIOLATION,
            message_args={"sql": sql, "error_message": str(e)},
        )

    # DataError = data-related errors (invalid data types, overflow, etc.)
    elif isinstance(e, DataError):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_ERROR,
            message_args={"sql": sql, "error_message": str(e)},
        )

    # InterfaceError/InternalError = connection-level problems
    elif isinstance(e, (InterfaceError, InternalError)):
        return DatusDbException(ErrorCode.DB_CONNECTION_FAILED, message_args={"error_message": str(e)})

    # OperationalError/DatabaseError = runtime errors (connection issues, query execution problems)
    elif isinstance(e, (OperationalError, DatabaseError)):
        return DatusDbException(
            ErrorCode.DB_EXECUTION_ERROR,
            message_args={"sql": sql, "error_message": str(e)},
        )

    # Catch-all for any other exceptions
    else:
        return DatusDbException(ErrorCode.DB_FAILED, message_args={"error_message": str(e)})


def _validate_sql_identifier(identifier: str, identifier_type: str = "identifier") -> None:
    """
    Validate SQL identifier to prevent SQL injection.

    Redshift identifiers must follow these rules:
    - Start with a letter (a-z, A-Z) or underscore (_)
    - Contain only letters, digits (0-9), underscores (_), or dollar signs ($)
    - Be 1-127 characters long
    - Cannot be a reserved word (but we allow it since it might be quoted)

    Args:
        identifier: The identifier to validate (schema, table, database name, etc.)
        identifier_type: Type of identifier for error messages (e.g., "schema", "table")

    Raises:
        ValueError: If identifier is invalid or contains potentially malicious characters
    """
    if not identifier:
        return  # Empty identifiers are allowed (means "not specified")

    # Check length (Redshift limit is 127 characters)
    if len(identifier) > 127:
        raise ValueError(
            f"Invalid {identifier_type} name: '{identifier}'. Maximum length is 127 characters, got {len(identifier)}."
        )

    # Check for valid characters: letters, digits, underscore, dollar sign
    # Pattern: Must start with letter or underscore, then any combination of alphanumeric, underscore, or dollar
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_$]*$", identifier):
        raise ValueError(
            f"Invalid {identifier_type} name: '{identifier}'. "
            f"Must start with a letter or underscore and contain only "
            f"alphanumeric characters, underscores, or dollar signs."
        )


class RedshiftConnector(BaseSqlConnector, SchemaNamespaceMixin, MaterializedViewSupportMixin, MigrationTargetMixin):
    """
    Connector for Amazon Redshift databases using native Redshift SDK.

    This connector provides full support for Redshift features including:
    - Multi-database and schema support (schemas are the main namespace in Redshift)
    - Tables, views, and materialized views
    - Efficient metadata retrieval using system tables
    - Connection with standard credentials or IAM authentication

    Inherits from:
    - BaseSqlConnector: Base functionality for SQL databases
    - SchemaNamespaceMixin: Support for database.schema.table naming
    - MaterializedViewSupportMixin: Support for materialized views

    Thread safety:
        This connector is NOT thread-safe when callers pass per-call
        ``catalog_name`` / ``database_name`` / ``schema_name`` overrides to
        ``execute_*`` methods. Those methods invoke ``do_switch_context``,
        which mutates the shared ``self.connection`` (via ``SET search_path``)
        and the shared ``self.schema_name`` attribute. Concurrent calls with
        different context overrides can interleave on the same connection and
        cause queries to run against the wrong schema.

        Recommended workarounds:
            - Serialize access to a single ``RedshiftConnector`` instance
              (e.g. with an external lock) when context overrides are used.
            - Create a separate ``RedshiftConnector`` per thread / request
              so each owns its own connection and context state.
    """

    def __init__(self, config: Union[RedshiftConfig, dict]):
        """
        Initialize Redshift connector.

        Args:
            config: RedshiftConfig object or dict with configuration parameters

        Raises:
            TypeError: If config is not RedshiftConfig or dict
        """

        # Convert dict to RedshiftConfig if needed (allows flexible initialization)
        if isinstance(config, dict):
            config = RedshiftConfig(**config)
        elif not isinstance(config, RedshiftConfig):
            raise TypeError(f"config must be RedshiftConfig or dict, got {type(config)}")

        # Store the configuration for later use
        self.redshift_config = config

        # Create connection configuration for the base class
        conn_config = ConnectionConfig(timeout_seconds=config.timeout_seconds)

        # Initialize the base class with Redshift dialect
        super().__init__(config=conn_config, dialect="redshift")

        # Build connection parameters dictionary
        connection_params = {
            "host": config.host,
            "port": config.port,
            "user": config.username,
            "password": config.password,
            "database": config.database if config.database else "dev",  # 'dev' is default Redshift database
            "timeout": config.timeout_seconds,
            "ssl": config.ssl,
        }

        # Add IAM authentication parameters if IAM is enabled
        if config.iam:
            connection_params.update(
                {
                    "iam": True,
                    "cluster_identifier": config.cluster_identifier,
                    "region": config.region,
                    "access_key_id": config.access_key_id,
                    "secret_access_key": config.secret_access_key,
                }
            )

        # Establish the connection to Redshift
        self.connection = redshift_connector.connect(**connection_params)

        # Store current context (database and schema)
        self.database_name = config.database or "dev"
        self.schema_name = config.schema_name or "public"

    def test_connection(self) -> Dict[str, Any]:
        """
        Test the database connection by executing a simple query.

        Returns:
            Dictionary with success status and message
        """
        try:
            # Execute a simple query to verify connection works
            with self.connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchall()
            return {
                "success": True,
                "message": "Connection successful",
                "database": self.database_name,
            }
        except Exception as e:
            # If connection test fails, raise appropriate exception
            raise _handle_redshift_exception(e, "SELECT 1")

    def close(self):
        """Close the database connection and free resources."""
        if self.connection:
            self.connection.close()

    def get_type(self) -> str:
        """
        Return the database type identifier.

        Returns:
            String identifier for Redshift
        """
        return "redshift"

    def _sys_databases(self) -> Set[str]:
        """
        Return set of system databases to filter out from listings.

        These are Redshift system databases that should be hidden from users.

        Returns:
            Set of system database names
        """
        return {"padb_harvest", "information_schema"}

    def _sys_schemas(self) -> Set[str]:
        """
        Return set of system schemas to filter out from listings.

        These are Redshift system schemas that should be hidden from users.

        Returns:
            Set of system schema names
        """
        return {"pg_catalog", "information_schema", "pg_internal"}

    def do_switch_context(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """
        Switch database or schema context.

        This changes the current working database/schema. Subsequent queries
        without fully-qualified names will use this context.

        Args:
            catalog_name: Catalog name (not used in Redshift)
            database_name: Database name to switch to
            schema_name: Schema name to switch to

        Raises:
            ValueError: If schema_name or database_name contains invalid characters
        """
        try:
            with self.connection.cursor() as cursor:
                # If schema_name is provided, set the search_path
                if schema_name:
                    # Validate schema name to prevent SQL injection
                    _validate_sql_identifier(schema_name, "schema")

                    # SET search_path changes which schema is used by default
                    sql = f'SET search_path TO "{schema_name}"'
                    cursor.execute(sql)
                    self.schema_name = schema_name

                # Note: Redshift doesn't support switching databases within a connection
                # You need to create a new connection to switch databases
                if database_name and database_name != self.database_name:
                    # Validate database name even though we're just warning
                    _validate_sql_identifier(database_name, "database")

                    logger.warning(
                        f"Cannot switch database from {self.database_name} to {database_name} "
                        f"in existing connection. Create a new connection to change databases."
                    )
        except ValueError as e:
            # Re-raise validation errors as-is
            raise e
        except Exception as e:
            raise _handle_redshift_exception(e, schema_name or database_name) from e

    def validate_input(self, input_params: Dict[str, Any]):
        """
        Validate input parameters before executing queries.

        Args:
            input_params: Dictionary of parameters to validate

        Raises:
            ValueError: If parameters are invalid
        """
        # Call base class validation first
        super().validate_input(input_params)

        # Additional validation: if params are provided, they must be a sequence or dict
        if "params" in input_params:
            if not isinstance(input_params["params"], Sequence) and not isinstance(input_params["params"], dict):
                raise ValueError("params must be dict or Sequence")

    def _do_execute_arrow(
        self, sql_query: str, params: Optional[Sequence[Any] | dict[Any, Any]] = None
    ) -> tuple[pa.Table, int]:
        """
        Execute SQL query and return results in Apache Arrow format.

        Arrow format is efficient for large datasets and enables fast data processing.

        Args:
            sql_query: SQL query to execute
            params: Optional query parameters for parameterized queries

        Returns:
            Tuple of (Arrow Table with results, row count)

        Raises:
            DatusDbException: If query execution fails
        """
        try:
            with self.connection.cursor() as cursor:
                # Execute the query with parameters if provided
                if params:
                    cursor.execute(sql_query, params)
                else:
                    cursor.execute(sql_query)

                # Fetch all results
                results = cursor.fetchall()

                # Get column names from cursor description
                column_names = [desc[0] for desc in cursor.description] if cursor.description else []

                # Convert results to Arrow table
                if results and column_names:
                    # Transpose rows to columns for Arrow
                    columns = list(zip(*results)) if results else [[] for _ in column_names]

                    # Create Arrow arrays for each column
                    arrow_arrays = [pa.array(col) for col in columns]

                    # Create Arrow table
                    arrow_table = pa.Table.from_arrays(arrow_arrays, names=column_names)
                    return arrow_table, len(results)
                else:
                    # Return empty table if no results
                    return pa.table([]), 0

        except Exception as e:
            raise _handle_redshift_exception(e, sql_query)

    def execute_query_to_df(
        self,
        sql: str,
        params: Sequence[Any] | dict[Any, Any] | None = None,
    ) -> DataFrame:
        """
        Execute query and return results as pandas DataFrame.

        Args:
            sql: SQL query to execute
            params: Optional query parameters

        Returns:
            Pandas DataFrame with query results
        """
        try:
            # Get Arrow table first
            arrow_table, _ = self._do_execute_arrow(sql, params)

            # Convert Arrow to pandas DataFrame
            return arrow_table.to_pandas()
        except DatusDbException:
            # Already normalized by _do_execute_arrow, just bubble up
            raise
        except Exception as e:
            # Normalize any new driver/runtime exceptions
            raise _handle_redshift_exception(e, sql) from e

    def execute_query_to_dict(self, sql: str) -> List[Dict[str, Any]]:
        """
        Execute query and return results as list of dictionaries.

        Each dictionary represents one row, with column names as keys.

        Args:
            sql: SQL query to execute

        Returns:
            List of dictionaries, one per row
        """
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                results = cursor.fetchall()

                # If no results, return empty list
                if not results:
                    return []

                # Get column names
                column_names = [desc[0] for desc in cursor.description]

                # Convert each row to dictionary
                result = []
                for row in results:
                    row_dict = {}
                    for i, col_name in enumerate(column_names):
                        row_dict[col_name] = row[i]
                    result.append(row_dict)

                return result
        except Exception as e:
            raise _handle_redshift_exception(e, sql)

    @override
    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """
        Execute DDL (Data Definition Language) statement.

        DDL statements include CREATE, ALTER, DROP, etc.

        Args:
            sql: DDL statement to execute

        Returns:
            ExecuteSQLResult with execution status
        """
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    @override
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """
        Execute INSERT statement.

        Args:
            sql: INSERT statement to execute

        Returns:
            ExecuteSQLResult with number of rows inserted
        """
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                # Commit the transaction
                self.connection.commit()

                # Get number of rows affected
                rowcount = cursor.rowcount if cursor.rowcount else 0

                return ExecuteSQLResult(
                    sql_query=sql,
                    row_count=rowcount,
                    sql_return=str(rowcount),
                    success=True,
                    error=None,
                )
        except Exception as e:
            # Rollback on error
            self.connection.rollback()
            ex = _handle_redshift_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                sql_query=sql,
                error=str(ex),
            )

    @override
    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """
        Execute UPDATE statement.

        Args:
            sql: UPDATE statement to execute

        Returns:
            ExecuteSQLResult with number of rows updated
        """
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    @override
    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """
        Execute DELETE statement.

        Args:
            sql: DELETE statement to execute

        Returns:
            ExecuteSQLResult with number of rows deleted
        """
        return self._execute_update_or_delete(
            sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
        )

    def _execute_update_or_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """
        Execute UPDATE, DELETE, or DDL statement.

        Internal method to handle all statements that modify data or schema.

        Args:
            sql: SQL statement to execute

        Returns:
            ExecuteSQLResult with execution status
        """
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                # Commit the transaction
                self.connection.commit()

                # Get number of rows affected
                rowcount = cursor.rowcount if cursor.rowcount else 0

                return ExecuteSQLResult(
                    sql_query=sql,
                    row_count=rowcount,
                    sql_return=str(rowcount),
                    success=True,
                    error=None,
                )
        except Exception as e:
            # Rollback on error
            self.connection.rollback()
            ex = _handle_redshift_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                sql_query=sql,
                error=str(ex),
            )

    @override
    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        """
        Execute context switch statement (SET search_path, etc.).

        Args:
            sql_query: Context switch SQL statement

        Returns:
            ExecuteSQLResult with execution status
        """
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql_query)

            # Parse the context switch to update internal state
            switch_context = parse_context_switch(sql=sql_query, dialect=self.dialect)
            if switch_context:
                if catalog_name := switch_context.get("catalog_name"):
                    self.catalog_name = catalog_name
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
            ex = _handle_redshift_exception(e, sql_query)
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
        """
        Execute query and return results in specified format.

        Args:
            sql: SQL query to execute
            result_format: Desired output format (csv, arrow, pandas, or list)

        Returns:
            ExecuteSQLResult with results in requested format
        """
        if catalog_name or database_name or schema_name:
            self.do_switch_context(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        # Route to appropriate execution method based on format
        if result_format == "csv":
            return self.execute_csv(sql)
        elif result_format == "pandas":
            return self.execute_pandas(sql)
        else:
            result = self.execute_arrow(sql)
            if result_format == "arrow":
                return result
            # Convert Arrow to list if requested
            if result and result.success:
                result.sql_return = result.sql_return.to_pylist()
                result.result_format = result_format
            return result

    def execute_arrow(self, sql: str, params: Optional[Sequence[Any] | dict[Any, Any]] = None) -> ExecuteSQLResult:
        """
        Execute query and return results as Arrow table.

        Args:
            sql: SQL query to execute
            params: Optional query parameters for parameterized queries

        Returns:
            ExecuteSQLResult with Arrow table
        """
        try:
            arrow_table, row_count = self._do_execute_arrow(sql, params)

            # Handle empty results
            if arrow_table is None:
                logger.debug(f"Arrow table is None for query. Row count: {row_count}")
                row_count = 0
                arrow_table = pa.table([])
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
        """
        Execute query and return results as pandas DataFrame.

        Args:
            sql: SQL query to execute

        Returns:
            ExecuteSQLResult with pandas DataFrame
        """
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
            # Already normalized, just convert to result
            return ExecuteSQLResult(success=False, sql_query=sql, result_format="pandas", error=str(e))

    def execute_csv(self, query: str) -> ExecuteSQLResult:
        """
        Execute query and return results as CSV string.

        Args:
            query: SQL query to execute

        Returns:
            ExecuteSQLResult with CSV formatted results
        """
        result = self.execute_pandas(query)
        result.result_format = "csv"
        # Convert DataFrame to CSV if query was successful
        if result.success and result.row_count > 0:
            result.sql_return = result.sql_return.to_csv(index=False)
        return result

    def execute_queries(self, queries: List[str]) -> List[ExecuteSQLResult]:
        """
        Execute multiple queries in sequence.

        Args:
            queries: List of SQL queries to execute

        Returns:
            List of ExecuteSQLResult, one per query
        """
        return [self.execute_query(sql) for sql in queries]

    def execute_queries_arrow(self, queries: List[str]) -> List[ExecuteSQLResult]:
        """
        Execute multiple queries and return Arrow results.

        Args:
            queries: List of SQL queries to execute

        Returns:
            List of ExecuteSQLResult with Arrow tables
        """
        return [self.execute_arrow(sql) for sql in queries]

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """
        Get list of databases in the Redshift cluster.

        Args:
            catalog_name: Catalog name (not used in Redshift)
            include_sys: Whether to include system databases

        Returns:
            List of database names
        """
        # Query system catalog to get database list
        sql = "SELECT datname FROM pg_database WHERE datistemplate = false"

        try:
            databases = []
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                results = cursor.fetchall()

                for row in results:
                    db_name = row[0]
                    # Filter out system databases if requested
                    if include_sys or db_name not in self._sys_databases():
                        databases.append(db_name)

            return databases
        except Exception as e:
            raise _handle_redshift_exception(e, sql)

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """
        Get list of schemas in the current database.

        Args:
            catalog_name: Catalog name (not used in Redshift)
            database_name: Database name (must match current database)
            include_sys: Whether to include system schemas

        Returns:
            List of schema names
        """
        # Query system catalog to get schema list
        sql = """
            SELECT nspname
            FROM pg_namespace
            WHERE nspname NOT LIKE 'pg_temp_%'
            AND nspname NOT LIKE 'pg_toast%'
        """

        try:
            schemas = []
            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                results = cursor.fetchall()

                for row in results:
                    schema_name = row[0]
                    # Filter out system schemas if requested
                    if include_sys or schema_name not in self._sys_schemas():
                        schemas.append(schema_name)

            return schemas
        except Exception as e:
            raise _handle_redshift_exception(e, sql)

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
        """
        Get list of table names.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used, uses current connection)
            schema_name: Schema name to query (default: current schema)

        Returns:
            List of table names
        """
        tables = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="table",
        )
        return [self._qualify_name(item, database_name, schema_name) for item in tables]

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """
        Get list of view names.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name to query

        Returns:
            List of view names
        """
        views = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="view",
        )
        return [self._qualify_name(view, database_name, schema_name) for view in views]

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """
        Get list of materialized view names.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name to query

        Returns:
            List of materialized view names
        """
        mvs = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="mv",
        )
        return [self._qualify_name(mv, database_name, schema_name) for mv in mvs]

    def _get_tables_per_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
        table_type: TABLE_TYPE = "",
    ) -> List[Dict[str, str]]:
        """
        Get table metadata from a schema.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name to query
            tables: Optional list of specific tables to retrieve
            table_type: Type of objects to retrieve (table, view, mv, or full for all)

        Returns:
            List of metadata dictionaries
        """
        schema_name = schema_name or self.schema_name
        result = []

        # Build the WHERE clause for schema filtering
        if schema_name:
            # Validate schema name to prevent SQL injection
            _validate_sql_identifier(schema_name, "schema")
            schema_filter = f"n.nspname = '{schema_name}'"
        else:
            # Exclude system schemas
            sys_schemas_str = ", ".join([f"'{s}'" for s in self._sys_schemas()])
            schema_filter = f"n.nspname NOT IN ({sys_schemas_str})"

        # Get tables if requested
        if table_type in ("table", "full"):
            # Validate all table names if provided to prevent SQL injection
            if tables:
                for table in tables:
                    _validate_sql_identifier(table, "table")

            # Query pg_class for tables (relkind = 'r')
            sql = f"""
                SELECT n.nspname as schema_name, c.relname as table_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND {schema_filter}
            """

            # Add table name filter if specific tables requested
            if tables:
                tables_str = ", ".join([f"'{t}'" for t in tables])
                sql += f" AND c.relname IN ({tables_str})"

            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    for row in cursor.fetchall():
                        result.append(
                            {
                                "catalog_name": "",
                                "database_name": self.database_name,
                                "schema_name": row[0],
                                "table_name": row[1],
                                "table_type": "table",
                                "identifier": self.identifier(
                                    database_name=self.database_name,
                                    schema_name=row[0],
                                    table_name=row[1],
                                ),
                            }
                        )
            except Exception as e:
                logger.warning(f"Failed to get tables: {e}")

        # Get views if requested
        if table_type in ("view", "full"):
            # Validate all view names if provided to prevent SQL injection
            if tables:
                for table in tables:
                    _validate_sql_identifier(table, "view")

            # Query pg_class for views (relkind = 'v')
            sql = f"""
                SELECT n.nspname as schema_name, c.relname as table_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'v' AND {schema_filter}
            """

            if tables:
                tables_str = ", ".join([f"'{t}'" for t in tables])
                sql += f" AND c.relname IN ({tables_str})"

            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    for row in cursor.fetchall():
                        result.append(
                            {
                                "catalog_name": "",
                                "database_name": self.database_name,
                                "schema_name": row[0],
                                "table_name": row[1],
                                "table_type": "view",
                                "identifier": self.identifier(
                                    database_name=self.database_name,
                                    schema_name=row[0],
                                    table_name=row[1],
                                ),
                            }
                        )
            except Exception as e:
                logger.warning(f"Failed to get views: {e}")

        # Get materialized views if requested
        if table_type in ("mv", "full"):
            # Validate all materialized view names if provided to prevent SQL injection
            if tables:
                for table in tables:
                    _validate_sql_identifier(table, "materialized view")

            # Query pg_class for materialized views (relkind = 'm')
            sql = f"""
                SELECT n.nspname as schema_name, c.relname as table_name
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'm' AND {schema_filter}
            """

            if tables:
                tables_str = ", ".join([f"'{t}'" for t in tables])
                sql += f" AND c.relname IN ({tables_str})"

            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    for row in cursor.fetchall():
                        result.append(
                            {
                                "catalog_name": "",
                                "database_name": self.database_name,
                                "schema_name": row[0],
                                "table_name": row[1],
                                "table_type": "mv",
                                "identifier": self.identifier(
                                    database_name=self.database_name,
                                    schema_name=row[0],
                                    table_name=row[1],
                                ),
                            }
                        )
            except Exception as e:
                logger.warning(f"Failed to get materialized views: {e}")

        return result

    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
        table_type: str = "table",
    ) -> List[Dict[str, Any]]:
        """
        Get schema information (columns) for a table/view/materialized view.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name
            table_name: Table name
            table_type: Type of object

        Returns:
            List of column information dictionaries
        """
        if not table_name:
            return []

        schema_name = schema_name or self.schema_name

        # Validate identifiers to prevent SQL injection
        _validate_sql_identifier(schema_name, "schema")
        _validate_sql_identifier(table_name, "table")

        # Query information_schema to get column information with comments
        sql = f"""
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                c.ordinal_position,
                d.description as column_comment
            FROM information_schema.columns c
            LEFT JOIN pg_catalog.pg_class cl ON cl.relname = c.table_name
            LEFT JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace AND n.nspname = c.table_schema
            LEFT JOIN pg_catalog.pg_attribute a ON a.attrelid = cl.oid AND a.attname = c.column_name
            LEFT JOIN pg_catalog.pg_description d ON d.objoid = cl.oid AND d.objsubid = a.attnum
            WHERE c.table_schema = '{schema_name}'
            AND c.table_name = '{table_name}'
            ORDER BY c.ordinal_position
        """

        try:
            schemas = []
            columns_list = []

            with self.connection.cursor() as cursor:
                cursor.execute(sql)
                results = cursor.fetchall()

                for idx, row in enumerate(results):
                    column_name = row[0]
                    data_type = row[1]
                    nullable = row[2] == "YES"
                    default_value = row[3]
                    comment = row[5] if len(row) > 5 else None

                    column_info = {
                        "cid": idx,
                        "name": column_name,
                        "type": data_type,
                        "nullable": nullable,
                        "pk": False,  # Would need to query pg_constraint for this
                        "default_value": default_value,
                        "comment": comment,
                    }

                    schemas.append(column_info)
                    columns_list.append({"name": column_name, "type": data_type})

            # Add summary information
            schemas.append(
                {
                    "table": table_name,
                    "columns": columns_list,
                    "table_type": table_type.lower(),
                }
            )

            return schemas
        except Exception as e:
            raise _handle_redshift_exception(e, sql)

    def _fetch_object_ddl(self, object_type: str, schema_name: str, table_name: str) -> str:
        """
        Retrieve DDL for a database object using pg_get_viewdef or similar.

        Args:
            object_type: Type of object (TABLE, VIEW, MATERIALIZED VIEW)
            schema_name: Schema name
            table_name: Object name

        Returns:
            DDL statement as string
        """
        try:
            # Validate identifiers to prevent SQL injection
            _validate_sql_identifier(schema_name, "schema")
            _validate_sql_identifier(table_name, "table")

            if object_type.upper() in ("VIEW", "MATERIALIZED VIEW"):
                # Use pg_get_viewdef to get view definition
                sql = f"SELECT pg_get_viewdef('{schema_name}.{table_name}', true)"
                with self.connection.cursor() as cursor:
                    cursor.execute(sql)
                    row = cursor.fetchone()
                    if row:
                        view_def = row[0]
                        mat_view = "MATERIALIZED " if "MATERIALIZED" in object_type.upper() else ""
                        return f"CREATE {mat_view}VIEW {schema_name}.{table_name} AS\n{view_def}"
            else:
                # For tables, we'd need to reconstruct DDL from system catalogs
                # This is complex, so we'll return a placeholder
                return f"-- DDL extraction for tables not fully implemented\n-- Table: {schema_name}.{table_name}"
        except Exception as e:
            logger.warning(f"Failed to get DDL for {object_type} {schema_name}.{table_name}: {e}")
            return f"-- DDL not available for {object_type.lower()} {schema_name}.{table_name}: {e}"

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """
        Get table metadata with DDL definitions.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name
            tables: Optional list of specific tables

        Returns:
            List of table metadata with DDL
        """
        table_entries = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            tables=tables,
            table_type="table",
        )

        if not table_entries:
            return []

        # Add DDL to each entry
        for entry in table_entries:
            entry["definition"] = self._fetch_object_ddl("TABLE", entry["schema_name"], entry["table_name"])

        return table_entries

    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """
        Get view metadata with DDL definitions.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name

        Returns:
            List of view metadata with DDL
        """
        view_entries = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="view",
        )

        if not view_entries:
            return []

        # Add DDL to each entry
        for entry in view_entries:
            entry["definition"] = self._fetch_object_ddl("VIEW", entry["schema_name"], entry["table_name"])

        return view_entries

    def get_materialized_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """
        Get materialized view metadata with DDL definitions.

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name

        Returns:
            List of materialized view metadata with DDL
        """
        mv_entries = self._get_tables_per_schema(
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
            table_type="mv",
        )

        if not mv_entries:
            return []

        # Add DDL to each entry
        for entry in mv_entries:
            entry["definition"] = self._fetch_object_ddl("MATERIALIZED VIEW", entry["schema_name"], entry["table_name"])

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
        """
        Get sample rows from tables.

        Args:
            tables: Optional list of specific tables
            top_n: Number of rows to sample per table
            catalog_name: Catalog name (not used)
            database_name: Database name (not used)
            schema_name: Schema name
            table_type: Type of objects to sample

        Returns:
            List of dictionaries with sample data
        """
        result = []
        schema_name = schema_name or self.schema_name

        with self.connection.cursor():
            # If specific tables provided, sample those
            if tables:
                for table in tables:
                    full_name = self.full_name(
                        catalog_name=catalog_name,
                        database_name=database_name,
                        schema_name=schema_name,
                        table_name=table,
                    )
                    sql = f"SELECT * FROM {full_name} LIMIT {top_n}"

                    try:
                        df = self.execute_query_to_df(sql)
                        if not df.empty:
                            result.append(
                                {
                                    "identifier": self.identifier(
                                        database_name=self.database_name,
                                        schema_name=schema_name,
                                        table_name=table,
                                    ),
                                    "catalog_name": "",
                                    "database_name": self.database_name,
                                    "schema_name": schema_name,
                                    "table_name": table,
                                    "table_type": table_type,
                                    "sample_rows": df.to_csv(index=False),
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Failed to get sample rows for {full_name}: {e}")
            else:
                # Sample all tables of the specified type
                for t in self._get_tables_per_schema(
                    catalog_name=catalog_name,
                    database_name=database_name,
                    schema_name=schema_name,
                    table_type=table_type,
                ):
                    full_table_name = self.full_name(
                        catalog_name=t["catalog_name"],
                        database_name=t["database_name"],
                        schema_name=t["schema_name"],
                        table_name=t["table_name"],
                    )
                    sql = f"SELECT * FROM {full_table_name} LIMIT {top_n}"

                    try:
                        df = self.execute_query_to_df(sql)
                        if not df.empty:
                            result.append(
                                {
                                    "identifier": t["identifier"],
                                    "catalog_name": t["catalog_name"],
                                    "database_name": t["database_name"],
                                    "schema_name": t["schema_name"],
                                    "table_name": t["table_name"],
                                    "table_type": t["table_type"],
                                    "sample_rows": df.to_csv(index=False),
                                }
                            )
                    except Exception as e:
                        logger.warning(f"Failed to get sample rows for {full_table_name}: {e}")

        return result

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """
        Build fully qualified table name.

        Format: "database_name"."schema_name"."table_name", "schema_name"."table_name", or just "table_name"

        Amazon Redshift supports cross-database queries using three-part qualification:
        database.schema.table (available on RA3 node types and serverless).

        Args:
            catalog_name: Catalog name (not used)
            database_name: Database name (for cross-database queries)
            schema_name: Schema name
            table_name: Table name

        Returns:
            Fully qualified table name with proper quoting
        """
        # Validate all provided identifiers to prevent SQL injection
        if database_name:
            _validate_sql_identifier(database_name, "database")
        if schema_name:
            _validate_sql_identifier(schema_name, "schema")
        if table_name:
            _validate_sql_identifier(table_name, "table")

        # If database and schema provided, use database.schema.table format (three-part qualification)
        if database_name and schema_name:
            return f'"{database_name}"."{schema_name}"."{table_name}"'
        # If only schema provided, use schema.table format (two-part qualification)
        elif schema_name:
            return f'"{schema_name}"."{table_name}"'
        else:
            # Just table name (will use current schema from search_path)
            return f'"{table_name}"'

    # ==================== MigrationTargetMixin ====================

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "redshift",
            "requires": [],  # DISTKEY/SORTKEY are strongly recommended but not required
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks-only)",
                "ENGINE = ... (MySQL/ClickHouse syntax)",
                "SERIAL (use IDENTITY(1,1) in Redshift)",
                "VARCHAR(MAX) (Redshift max is 65535; length must be explicit)",
            ],
            "type_hints": {
                "DISTKEY": "Add DISTKEY(<col>) for even data distribution; prefer a high-cardinality join key",
                "SORTKEY": "Add SORTKEY(<col>) for date/timestamp columns used in filters",
                "ENCODE": "Add ENCODE <scheme> per column for compression; AZ64 is a good default",
                "unbounded VARCHAR": "VARCHAR with explicit length (max 65535)",
                "JSON": "SUPER (preferred for semi-structured data)",
                "JSONB": "SUPER",
                "HUGEINT": "NUMERIC(38,0)",
                "LARGEINT": "NUMERIC(38,0)",
                "BOOLEAN": "BOOLEAN",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
            },
            "example_ddl": (
                "CREATE TABLE public.t (\n"
                "  id BIGINT IDENTITY(1,1),\n"
                "  event_date DATE,\n"
                "  amount NUMERIC(18,2)\n"
                ")\n"
                "DISTKEY(id)\n"
                "SORTKEY(event_date)"
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Suggest DISTKEY (high-cardinality key) + SORTKEY (date/time columns)."""
        if not columns:
            return {}
        import re as _re

        _INT_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "INT2", "INT4", "INT8"}

        # DISTKEY preference: *_id integer columns → any INT column → first column (preserve input order)
        distkey = None
        scored = []
        for idx, col in enumerate(columns):
            name = col["name"]
            col_type = str(col.get("type", "")).upper()
            base_type = _re.sub(r"\(.*\)", "", col_type).strip()
            score = 0
            if name.lower() == "id" or name.lower().endswith("_id"):
                score += 100
            if base_type in _INT_TYPES:
                score += 50
            scored.append((score, idx, name))
        # Sort by score desc, then by input order (stable fallback to first column when all scored 0)
        scored.sort(key=lambda x: (-x[0], x[1]))
        distkey = scored[0][2] if scored else columns[0]["name"]

        # SORTKEY preference: date/time columns
        sortkey: List[str] = []
        for col in columns:
            base_type = _re.sub(r"\(.*\)", "", str(col.get("type", "")).upper()).strip()
            if base_type in ("DATE", "DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIME"):
                sortkey.append(col["name"])

        layout: Dict[str, Any] = {"distkey": distkey}
        if sortkey:
            layout["sortkey"] = sortkey[:2]  # Redshift compound sortkey max is reasonable to keep small
        return layout

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()
        if "DUPLICATE KEY" in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; Redshift uses DISTKEY/SORTKEY")
        if "BUCKETS" in upper and "DISTRIBUTED BY" in upper:
            errors.append("DISTRIBUTED BY ... BUCKETS is StarRocks syntax; Redshift uses DISTKEY")
        if "ENGINE =" in upper or "ENGINE=" in upper:
            errors.append("ENGINE clause is MySQL/ClickHouse syntax; not supported by Redshift")
        import re as _re

        # Word-boundary match covers SERIAL, BIGSERIAL, SMALLSERIAL while avoiding
        # false positives on column names like SERIAL_NUMBER.
        if _re.search(r"\b(?:BIG|SMALL)?SERIAL\b", upper):
            errors.append("Redshift does not support SERIAL/BIGSERIAL/SMALLSERIAL; use IDENTITY(1,1) instead")
        if "VARCHAR(MAX)" in upper.replace(" ", ""):
            errors.append("Redshift does not support VARCHAR(MAX); use VARCHAR(<explicit length up to 65535>)")
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "NUMERIC(38,0)",
            "LARGEINT": "NUMERIC(38,0)",
            "JSON": "SUPER",
            "JSONB": "SUPER",
            "VARIANT": "SUPER",
            "DATETIME": "TIMESTAMP",
        }
        return overrides.get(base)
