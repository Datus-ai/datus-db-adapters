# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple, override

from pandas import DataFrame
from pyarrow import Table
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Inspector
from sqlalchemy.exc import (
    DatabaseError,
    DataError,
    IntegrityError,
    InterfaceError,
    InternalError,
    NoSuchTableError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
    SQLAlchemyError,
    TimeoutError,
)

from datus_db_core import (
    TABLE_TYPE,
    BaseSqlConnector,
    ConnectionConfig,
    DatusDbException,
    ErrorCode,
    ExecuteSQLResult,
    MigrationTargetMixin,
    SQLType,
    get_logger,
    parse_context_switch,
    parse_sql_type,
)

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


class SQLAlchemyConnector(BaseSqlConnector, MigrationTargetMixin):
    """
    Base SQLAlchemy connector for database adapters.

    Thread-safe: each operation checks out a connection from the engine pool,
    applies the current thread's context via do_switch_context(), executes,
    and returns the connection to the pool.
    """

    def __init__(self, connection_string: str, dialect: str = "", timeout_seconds: int = 30):
        """
        Initialize SQLAlchemyConnector.

        Args:
            connection_string: SQLAlchemy connection string
            dialect: Database dialect (mysql, postgresql, etc.)
            timeout_seconds: Connection timeout in seconds
        """
        # Auto-detect dialect from connection string if not provided
        if not dialect:
            prefix = connection_string.split(":")[0] if isinstance(connection_string, str) else "unknown"
            dialect = "mysql" if prefix == "mysql+pymysql" else prefix

        config = ConnectionConfig(timeout_seconds=timeout_seconds)
        super().__init__(config, dialect)
        self.connection_string = connection_string
        self.engine = None
        self._owns_engine = False
        self._engine_lock = threading.Lock()

    def __del__(self):
        """Destructor to ensure engine is properly disposed."""
        try:
            self.close()
        except Exception:
            pass

    # ==================== Connection Management ====================

    def _ensure_engine(self):
        """Create the SQLAlchemy engine with connection pool if not exists.

        Returns the engine to use. Callers must use the return value rather
        than ``self.engine`` to avoid races when subclasses maintain multiple
        engines (e.g. PostgreSQL engine-per-database).
        """
        if self.engine and self._owns_engine:
            return self.engine
        with self._engine_lock:
            # Double-check after acquiring lock
            if self.engine and self._owns_engine:
                return self.engine
            try:
                if self.dialect not in ("duckdb", "sqlite"):
                    self.engine = create_engine(
                        self.connection_string,
                        pool_size=10,
                        max_overflow=20,
                        pool_timeout=self.timeout_seconds,
                        pool_recycle=3600,
                        pool_pre_ping=True,
                    )
                else:
                    self.engine = create_engine(self.connection_string)
                self._owns_engine = True
                return self.engine
            except Exception as e:
                self.engine = None
                self._owns_engine = False
                raise self._handle_exception(e, "", "connection") from e

    @override
    def connect(self):
        """Backward-compatible alias for _ensure_engine()."""
        self._ensure_engine()

    @contextmanager
    def _conn(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Checkout a connection from pool with context applied.

        Args:
            catalog_name: Override thread-local catalog (empty = use thread-local)
            database_name: Override thread-local database (empty = use thread-local)
            schema_name: Override thread-local schema (empty = use thread-local)

        Usage:
            with self._conn(database_name="db1") as conn:
                result = conn.execute(text(sql))
        """
        effective_catalog = catalog_name or self.catalog_name
        effective_database = database_name or self.database_name
        effective_schema = schema_name or self.schema_name
        engine = self._ensure_engine()
        conn = engine.connect()
        try:
            self.do_switch_context(
                conn,
                catalog_name=effective_catalog,
                database_name=effective_database,
                schema_name=effective_schema,
            )
            yield conn
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()

    @override
    def close(self):
        """Dispose the engine and its connection pool."""
        try:
            engine = getattr(self, "engine", None)
            if engine:
                engine.dispose()
            self.engine = None
            self._owns_engine = False
        except Exception as e:
            logger.warning(f"Error disposing engine: {str(e)}")

    # ==================== Error Handling ====================

    def _handle_exception(self, e: Exception, sql: str = "", operation: str = "SQL execution") -> DatusDbException:
        """Map SQLAlchemy exceptions to Datus exceptions."""
        if isinstance(e, DatusDbException):
            return e
        _log_sql_exception(f"{operation} failed", sql, e)

        # Extract error message
        if hasattr(e, "detail") and e.detail:
            error_message = str(e.detail) if not isinstance(e.detail, list) else "\n".join(e.detail)
        elif hasattr(e, "orig") and e.orig is not None:
            error_message = str(e.orig)
        else:
            error_message = str(e)

        message_args = {"error_message": error_message, "sql": sql}
        error_msg_lower = error_message.lower()

        # Syntax errors
        if any(kw in error_msg_lower for kw in ["syntax", "parse error", "sql error"]):
            return DatusDbException(ErrorCode.DB_EXECUTION_SYNTAX_ERROR, message_args=message_args)

        # Table not found
        if isinstance(e, NoSuchTableError):
            return DatusDbException(ErrorCode.DB_TABLE_NOT_EXISTS, message_args={"table_name": str(e)})

        # Connection and operational errors
        if isinstance(e, (OperationalError, InterfaceError)):
            # Transaction rollback errors
            if any(kw in error_msg_lower for kw in ["invalid transaction", "can't reconnect"]):
                logger.warning("Invalid transaction state detected")
                return DatusDbException(ErrorCode.DB_TRANSACTION_FAILED, message_args=message_args)

            # Timeout errors
            if any(kw in error_msg_lower for kw in ["timeout", "timed out"]):
                return DatusDbException(ErrorCode.DB_CONNECTION_TIMEOUT, message_args=message_args)

            # Authentication errors
            if any(kw in error_msg_lower for kw in ["authentication", "access denied", "login failed"]):
                return DatusDbException(ErrorCode.DB_AUTHENTICATION_FAILED, message_args=message_args)

            # Permission errors
            if any(kw in error_msg_lower for kw in ["permission denied", "insufficient privilege"]):
                message_args["operation"] = operation
                return DatusDbException(ErrorCode.DB_PERMISSION_DENIED, message_args=message_args)

            # Connection errors
            if any(kw in error_msg_lower for kw in ["connection refused", "connection failed", "unable to open"]):
                return DatusDbException(ErrorCode.DB_CONNECTION_FAILED, message_args=message_args)

            return DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message_args=message_args)

        # Programming errors
        if isinstance(e, ProgrammingError):
            if any(kw in error_msg_lower for kw in ["syntax", "parse error", "sql error"]):
                return DatusDbException(ErrorCode.DB_EXECUTION_SYNTAX_ERROR, message_args=message_args)
            return DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message_args=message_args)

        # Constraint violations
        if isinstance(e, IntegrityError):
            return DatusDbException(ErrorCode.DB_CONSTRAINT_VIOLATION, message_args=message_args)

        # Timeout errors
        if isinstance(e, TimeoutError):
            return DatusDbException(ErrorCode.DB_EXECUTION_TIMEOUT, message_args=message_args)

        # Other database errors
        if isinstance(e, (DatabaseError, DataError, InternalError, NotSupportedError)):
            return DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message_args=message_args)

        # Fallback
        return DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message_args=message_args)

    # ==================== Core Execute Methods ====================

    @override
    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        """Execute SELECT query."""
        try:
            result = self._execute_query(
                sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
            )
            row_count = len(result)

            # Format result based on requested format
            if result_format == "csv":
                df = DataFrame(result)
                result = df.to_csv(index=False)
            elif result_format == "arrow":
                result = Table.from_pylist(result)
            elif result_format == "pandas":
                result = DataFrame(result)

            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=result,
                row_count=row_count,
                result_format=result_format,
            )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql)

    def _execute_query(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, Any]]:
        """Internal query execution returning list of dicts."""
        if parse_sql_type(sql, self.dialect) in (
            SQLType.INSERT,
            SQLType.UPDATE,
            SQLType.DELETE,
            SQLType.MERGE,
            SQLType.CONTENT_SET,
            SQLType.UNKNOWN,
        ):
            raise DatusDbException(
                ErrorCode.DB_EXECUTION_ERROR,
                message="Only SELECT and metadata queries are supported",
            )

        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                result = conn.execute(text(sql))
                rows = result.fetchall()
                return [row._asdict() for row in rows]
        except DatusDbException:
            raise
        except Exception as e:
            raise self._handle_exception(e, sql, "query") from e

    @override
    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute INSERT statement."""
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                res = conn.execute(text(sql))
                conn.commit()

                # Get inserted primary key or row count
                inserted_pk = None
                try:
                    if hasattr(res, "inserted_primary_key") and res.inserted_primary_key:
                        inserted_pk = res.inserted_primary_key
                except Exception:
                    pass

                lastrowid = getattr(res, "lastrowid", None)
                return_value = inserted_pk if inserted_pk else (lastrowid if lastrowid else res.rowcount)

                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=str(return_value),
                    row_count=res.rowcount,
                )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql, sql_return="", row_count=0)

    @override
    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute UPDATE statement."""
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                res = conn.execute(text(sql))
                conn.commit()
                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=str(res.rowcount),
                    row_count=res.rowcount,
                )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql, sql_return="", row_count=0)

    @override
    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute DELETE statement."""
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                res = conn.execute(text(sql))
                conn.commit()
                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=str(res.rowcount),
                    row_count=res.rowcount,
                )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql, sql_return="", row_count=0)

    @override
    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute DDL statement (CREATE, ALTER, DROP, etc.)."""
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                res = conn.execute(text(sql))
                conn.commit()
                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=str(res.rowcount),
                    row_count=res.rowcount,
                )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, sql_query=sql, error=str(ex))

    def execute_pandas(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute query and return pandas DataFrame."""
        try:
            df = self._execute_pandas(
                sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
            )
            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=df,
                row_count=len(df),
                result_format="pandas",
            )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql)

    def _execute_pandas(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> DataFrame:
        """Internal pandas execution."""
        return DataFrame(
            self._execute_query(sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name)
        )

    def execute_csv(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute query and return CSV format."""
        try:
            df = self._execute_pandas(
                sql, catalog_name=catalog_name, database_name=database_name, schema_name=schema_name
            )
            return ExecuteSQLResult(
                success=True,
                sql_query=sql,
                sql_return=df.to_csv(index=False),
                row_count=len(df),
                result_format="csv",
            )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                sql_query=sql,
                sql_return="",
                row_count=0,
                error=str(ex),
                result_format="csv",
            )

    def execute_arrow(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        """Execute query and return Arrow table."""
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                result = conn.execute(text(sql))
                if result.returns_rows:
                    df = DataFrame(result.fetchall(), columns=result.keys())
                    table = Table.from_pandas(df)
                    return ExecuteSQLResult(
                        success=True,
                        sql_query=sql,
                        sql_return=table,
                        row_count=len(df),
                        result_format="arrow",
                    )
                return ExecuteSQLResult(
                    success=True,
                    sql_query=sql,
                    sql_return=result.rowcount,
                    row_count=0,
                    result_format="arrow",
                )
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(
                success=False,
                error=str(ex),
                sql_query=sql,
                sql_return="",
                row_count=0,
                result_format="arrow",
            )

    @override
    def execute_content_set(self, sql: str) -> ExecuteSQLResult:
        """Execute USE/SET commands."""
        try:
            with self._conn() as conn:
                conn.execute(text(sql))
                conn.commit()

            # Update thread-local context if applicable
            if self.dialect != "sqlite":
                context = parse_context_switch(sql=sql, dialect=self.dialect)
                if context:
                    if catalog := context.get("catalog_name"):
                        self.catalog_name = catalog
                    if database := context.get("database_name"):
                        self.database_name = database
                    if schema := context.get("schema_name"):
                        self.schema_name = schema

            return ExecuteSQLResult(success=True, sql_query=sql, sql_return="Successful", row_count=0)
        except Exception as e:
            ex = e if isinstance(e, DatusDbException) else self._handle_exception(e, sql)
            return ExecuteSQLResult(success=False, error=str(ex), sql_query=sql)

    def execute_queries(
        self, queries: List[str], catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Any]:
        """Execute multiple queries on a single connection (batch atomicity)."""
        results = []
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                for query in queries:
                    result = conn.execute(text(query))
                    if result.returns_rows:
                        df = DataFrame(result.fetchall(), columns=list(result.keys()))
                        results.append(df.to_dict(orient="records"))
                    else:
                        query_lower = query.strip().lower()
                        if query_lower.startswith("insert"):
                            inserted_pk = None
                            try:
                                if hasattr(result, "inserted_primary_key") and result.inserted_primary_key:
                                    inserted_pk = result.inserted_primary_key
                            except Exception:
                                pass
                            lastrowid = getattr(result, "lastrowid", None)
                            results.append(
                                inserted_pk if inserted_pk else (lastrowid if lastrowid else result.rowcount)
                            )
                        elif query_lower.startswith(("update", "delete")):
                            results.append(result.rowcount)
                        else:
                            results.append(None)
                conn.commit()
        except SQLAlchemyError as e:
            raise self._handle_exception(e, "\n".join(queries), "batch query") from e
        return results

    def test_connection(self) -> bool:
        """Test database connection."""
        try:
            with self._conn() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            if isinstance(e, DatusDbException):
                raise
            raise DatusDbException(
                ErrorCode.DB_CONNECTION_FAILED,
                message_args={"error_message": "Connection test failed"},
            ) from e

    # ==================== Metadata Methods ====================

    def _inspector(self) -> Inspector:
        """Get SQLAlchemy inspector."""
        engine = self._ensure_engine()
        try:
            return inspect(engine)
        except Exception as e:
            raise self._handle_exception(e, operation="inspector creation") from e

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
        """Get list of tables."""
        sqlalchemy_schema = self._sqlalchemy_schema(catalog_name, database_name, schema_name)
        inspector = self._inspector()
        real_schema = sqlalchemy_schema or getattr(inspector, "default_schema_name", "") or ""
        return [
            self._qualify(name, "", real_schema, "", sqlalchemy_schema)
            for name in inspector.get_table_names(schema=sqlalchemy_schema)
        ]

    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of views."""
        sqlalchemy_schema = self._sqlalchemy_schema(catalog_name, database_name, schema_name)
        inspector = self._inspector()
        try:
            real_schema = sqlalchemy_schema or getattr(inspector, "default_schema_name", "") or ""
            return [
                self._qualify(name, "", real_schema, "", sqlalchemy_schema)
                for name in inspector.get_view_names(schema=sqlalchemy_schema)
            ]
        except Exception as e:
            raise DatusDbException(
                ErrorCode.DB_FAILED,
                message_args={"operation": "get_views", "error_message": str(e)},
            ) from e

    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of schemas."""
        schemas = self._inspector().get_schema_names()
        if not include_sys:
            system_schemas = self._sys_schemas()
            schemas = [s for s in schemas if s.lower() not in system_schemas]
        return schemas

    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get table schema information."""
        sqlalchemy_schema = self._sqlalchemy_schema(
            catalog_name or self.catalog_name,
            database_name or self.database_name,
            schema_name or self.schema_name,
        )
        inspector = self._inspector()
        try:
            schemas: List[Dict[str, Any]] = []
            pk_columns = set(
                inspector.get_pk_constraint(table_name=table_name, schema=sqlalchemy_schema)["constrained_columns"]
            )
            columns = inspector.get_columns(table_name=table_name, schema=sqlalchemy_schema)
            for i, col in enumerate(columns):
                schemas.append(
                    {
                        "cid": i,
                        "name": col["name"],
                        "type": str(col["type"]),
                        "comment": str(col["comment"]) if "comment" in col else None,
                        "nullable": col["nullable"],
                        "pk": col["name"] in pk_columns,
                        "default_value": col["default"],
                    }
                )
            return schemas
        except Exception as e:
            raise self._handle_exception(e, sql="", operation="get schema") from e

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """Get list of materialized views."""
        inspector = self._inspector()
        try:
            if hasattr(inspector, "get_materialized_view_names"):
                sqlalchemy_schema = self._sqlalchemy_schema(catalog_name, database_name, schema_name)
                real_schema = sqlalchemy_schema or getattr(inspector, "default_schema_name", "") or ""
                return [
                    self._qualify(name, "", real_schema, "", sqlalchemy_schema)
                    for name in inspector.get_materialized_view_names(schema=sqlalchemy_schema)
                ]
            return []
        except Exception as e:
            logger.debug(f"Materialized views not supported: {str(e)}")
            return []

    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, str]]:
        """Get sample data from tables."""
        self._inspector()
        try:
            samples = []
            if not tables:
                tables = []
                if table_type in ("table", "full"):
                    tables.extend(self.get_tables(catalog_name, database_name, schema_name))
                if table_type in ("view", "full"):
                    tables.extend(self.get_views(catalog_name, database_name, schema_name))
                if table_type in ("mv", "full"):
                    try:
                        tables.extend(self.get_materialized_views(catalog_name, database_name, schema_name))
                    except Exception as e:
                        logger.debug(f"Materialized views not supported: {e}")

            logger.info(f"Getting sample data from {len(tables)} tables, limit {top_n}")
            for listed_name in tables:
                # get_tables/get_views/get_materialized_views now return qualified
                # ``[db.][schema.]table`` names; strip back to the bare table so
                # full_name()/identifier() don't double-qualify the coordinates.
                table_name = listed_name.split(".")[-1]
                full_name = self.full_name(catalog_name, database_name, schema_name, table_name)
                query = f"SELECT * FROM {full_name} LIMIT {top_n}"
                result = self._execute_pandas(query)
                if not result.empty:
                    samples.append(
                        {
                            "identifier": self.identifier(catalog_name, database_name, schema_name, table_name),
                            "catalog_name": catalog_name,
                            "database_name": database_name,
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "table_type": table_type,
                            "sample_rows": result.to_csv(index=False),
                        }
                    )
            return samples
        except DatusDbException:
            raise
        except Exception as e:
            raise self._handle_exception(e) from e

    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        """Get schema name for SQLAlchemy Inspector."""
        return database_name or schema_name

    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Build fully-qualified table name."""
        return self.identifier(catalog_name, database_name, schema_name, table_name)

    # ==================== Streaming Methods ====================

    def execute_csv_iterator(
        self,
        sql: str,
        max_rows: int = 100,
        with_header: bool = True,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> Iterator[Tuple]:
        """Execute query and return CSV rows in batches.

        Warning: The underlying pool connection stays checked out for the
        lifetime of this generator.  Callers must fully consume the iterator
        or explicitly call ``.close()`` on it to return the connection to
        the pool.  Abandoning an unconsumed iterator may starve the pool
        until the generator is garbage-collected.
        """
        try:
            with self._conn(catalog_name=catalog_name, database_name=database_name, schema_name=schema_name) as conn:
                result = conn.execute(text(sql).execution_options(stream_results=True, max_row_buffer=max_rows))
                if result.returns_rows:
                    if with_header:
                        yield result.keys()
                    while True:
                        batch_rows = result.fetchmany(max_rows)
                        if not batch_rows:
                            break
                        for row in batch_rows:
                            yield row
                else:
                    if with_header:
                        yield ()
                    yield from []
        except Exception as e:
            raise self._handle_exception(e, sql, "streaming query") from e

    # ==================== MigrationTargetMixin (generic fallback) ====================
    #
    # Conservative OLTP-style defaults. SQLAlchemy-backed adapters that have
    # specific dialect needs (MySQL/Postgres/ClickHouse/Snowflake/Redshift/...)
    # should override these methods; their overrides take precedence.

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "sqlalchemy-generic",
            "requires": [],
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks-only)",
            ],
            "type_hints": {
                "unbounded VARCHAR": "VARCHAR with explicit length; many engines reject unbounded VARCHAR",
                "HUGEINT": "DECIMAL(38,0)",
                "LARGEINT": "DECIMAL(38,0)",
            },
            "example_ddl": "CREATE TABLE t (id BIGINT PRIMARY KEY, name VARCHAR(255))",
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Generic fallback: no distribution / partition hints. LLM decides.
        return {}

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()
        if "DUPLICATE KEY" in upper and "ON DUPLICATE KEY" not in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; not supported by generic SQLAlchemy target")
        if "BUCKETS" in upper and "DISTRIBUTED BY" in upper:
            errors.append("DISTRIBUTED BY ... BUCKETS is StarRocks syntax; not supported by generic SQLAlchemy target")
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "DECIMAL(38,0)",
            "LARGEINT": "DECIMAL(38,0)",
        }
        return overrides.get(base)
