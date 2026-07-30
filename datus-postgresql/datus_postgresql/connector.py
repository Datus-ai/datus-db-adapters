# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Union, override
from urllib.parse import quote_plus

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from datus_db_core import (
    TABLE_TYPE,
    DatusDbException,
    ErrorCode,
    MigrationTargetMixin,
    get_logger,
    list_to_in_str,
)
from datus_sqlalchemy import SQLAlchemyConnector

from .config import PostgreSQLConfig

logger = get_logger(__name__)


class TableMetadataNames(BaseModel):
    """Metadata configuration for different PostgreSQL object types."""

    info_table: str = Field(..., description="INFORMATION_SCHEMA table name or pg_catalog view")
    table_types: Optional[List[str]] = Field(default=None, description="TABLE_TYPE values in INFORMATION_SCHEMA")


# Metadata configuration for PostgreSQL objects
METADATA_DICT: Dict[TABLE_TYPE, TableMetadataNames] = {
    "table": TableMetadataNames(
        info_table="tables",
        table_types=["BASE TABLE"],
    ),
    "view": TableMetadataNames(
        info_table="views",
    ),
    "mv": TableMetadataNames(
        info_table="pg_matviews",
    ),
}


def _get_metadata_config(table_type: TABLE_TYPE) -> TableMetadataNames:
    """Get metadata configuration for given table type."""
    if table_type not in METADATA_DICT:
        raise DatusDbException(ErrorCode.COMMON_FIELD_INVALID, f"Invalid table type '{table_type}'")
    return METADATA_DICT[table_type]


class PostgreSQLConnector(SQLAlchemyConnector, MigrationTargetMixin):
    """PostgreSQL database connector."""

    def __init__(self, config: Union[PostgreSQLConfig, dict]):
        """
        Initialize PostgreSQL connector.

        Args:
            config: PostgreSQLConfig object or dict with configuration
        """
        # Handle config object or dict
        if isinstance(config, dict):
            config = PostgreSQLConfig(**config)
        elif not isinstance(config, PostgreSQLConfig):
            raise TypeError(f"config must be PostgreSQLConfig or dict, got {type(config)}")

        self.host = config.host
        self.port = config.port
        self.username = config.username
        self.password = config.password
        database = config.database or "postgres"

        # URL encode username and password to handle special characters
        encoded_username = quote_plus(self.username) if self.username else ""
        encoded_password = quote_plus(self.password) if self.password else ""

        # Build connection string
        connection_string = (
            f"postgresql+psycopg2://{encoded_username}:{encoded_password}@{self.host}:{self.port}/"
            f"{database}?sslmode={config.sslmode}"
        )

        super().__init__(
            connection_string,
            dialect="postgresql",
            timeout_seconds=config.timeout_seconds,
        )
        # Set after super().__init__() so BaseSqlConnector doesn't overwrite
        # with a plain ConnectionConfig (which lacks sslmode, etc.)
        self.config = config
        self._default_database = database
        self._default_schema = config.schema_name or "public"
        self._engines: OrderedDict = OrderedDict()  # LRU cache: database_name -> engine
        self._max_engines = 8

    # ==================== System Resources ====================

    @override
    def _sys_databases(self) -> Set[str]:
        """System databases to filter out."""
        return {"template0", "template1"}

    @override
    def _sys_schemas(self) -> Set[str]:
        """System schemas to filter out."""
        return {
            "pg_catalog",
            "information_schema",
            "pg_toast",
            "pg_temp_1",
            "pg_toast_temp_1",
        }

    # ==================== Utility Methods ====================

    # quote_identifier: uses BaseSqlConnector default (ANSI double quotes)

    def _build_connection_string(self, database_name: str) -> str:
        """Build a PostgreSQL connection string for a given database."""
        encoded_username = quote_plus(self.username) if self.username else ""
        encoded_password = quote_plus(self.password) if self.password else ""
        return (
            f"postgresql+psycopg2://{encoded_username}:{encoded_password}"
            f"@{self.host}:{self.port}/{database_name}?sslmode={self.config.sslmode}"
        )

    # ==================== Metadata Retrieval ====================

    def _get_metadata(
        self,
        table_type: TABLE_TYPE = "table",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        """
        Get metadata for tables/views from INFORMATION_SCHEMA or pg_catalog.

        Args:
            table_type: Type of object (table, view, mv)
            catalog_name: Catalog name (unused in PostgreSQL)
            database_name: Database in which to inspect the objects
            schema_name: Schema name to query

        Returns:
            List of metadata dictionaries
        """
        self.connect()
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name

        # Get metadata configuration
        metadata_config = _get_metadata_config(table_type)

        safe_schema = schema_name.replace("'", "''") if schema_name else ""
        sys_schemas = list(self._sys_schemas())

        if table_type == "mv":
            # pg_matviews is scoped to the current database connection.
            # Use a temporary connection if a different database is requested (thread-safe).
            base_query = "SELECT schemaname as table_schema, matviewname as table_name FROM pg_matviews"

            def _build_mv(case_insensitive: bool) -> str:
                if schema_name:
                    cmp = (
                        f"lower(schemaname) = lower('{safe_schema}')"
                        if case_insensitive
                        else f"schemaname = '{safe_schema}'"
                    )
                else:
                    cmp = list_to_in_str("schemaname not in", sys_schemas)
                return f"{base_query} WHERE {cmp}"

            query_result = self._execute_pandas(_build_mv(False), database_name=database_name)
            if len(query_result) == 0 and schema_name:
                query_result = self._execute_pandas(_build_mv(True), database_name=database_name)
                self._raise_if_ambiguous_schema(query_result, schema_name)
        else:
            # Tables and views use information_schema (supports table_catalog filter)
            safe_db = database_name.replace("'", "''") if database_name else ""
            type_filter = (
                list_to_in_str("and table_type in", metadata_config.table_types) if table_type == "table" else ""
            )
            base_query = f"SELECT table_schema, table_name FROM information_schema.{metadata_config.info_table}"

            def _build_tv(case_insensitive: bool) -> str:
                if schema_name:
                    cmp = (
                        f"lower(table_schema) = lower('{safe_schema}')"
                        if case_insensitive
                        else f"table_schema = '{safe_schema}'"
                    )
                else:
                    cmp = list_to_in_str("table_schema not in", sys_schemas)
                return f"{base_query} WHERE table_catalog = '{safe_db}' AND {cmp} {type_filter}"

            query_result = self._execute_pandas(_build_tv(False), database_name=database_name)
            if len(query_result) == 0 and schema_name:
                query_result = self._execute_pandas(_build_tv(True), database_name=database_name)
                self._raise_if_ambiguous_schema(query_result, schema_name)

        # Format results
        result = []
        for i in range(len(query_result)):
            schema = query_result["table_schema"][i]
            tb_name = query_result["table_name"][i]
            result.append(
                {
                    "identifier": self.identifier(
                        database_name=database_name,
                        schema_name=schema,
                        table_name=tb_name,
                    ),
                    "catalog_name": "",
                    "database_name": database_name,
                    "schema_name": schema,
                    "table_name": tb_name,
                    "table_type": table_type,
                }
            )
        return result

    def _get_ddl(
        self,
        schema_name: str,
        table_name: str,
        object_type: str = "TABLE",
        database_name: str = "",
    ) -> str:
        """
        Get DDL for a table/view using pg_get_tabledef or reconstructing from metadata.

        Args:
            schema_name: Schema name
            table_name: Table name
            object_type: Object type (TABLE, VIEW, MATERIALIZED VIEW)
            database_name: Database in which to inspect the object

        Returns:
            DDL statement as string
        """
        database_name = database_name or self.database_name
        full_name = self.full_name(
            database_name=database_name,
            schema_name=schema_name,
            table_name=table_name,
        )

        safe_schema = schema_name.replace("'", "''") if schema_name else ""
        safe_table = table_name.replace("'", "''") if table_name else ""

        if object_type == "VIEW":
            # Get view definition
            sql = f"""
                SELECT pg_get_viewdef('{safe_schema}.{safe_table}'::regclass, true) as definition
            """
            result = self._execute_pandas(sql, database_name=database_name)
            if not result.empty and result["definition"][0]:
                return f"CREATE VIEW {full_name} AS\n{result['definition'][0]}"
            return f"-- DDL not available for {full_name}"

        elif object_type == "MATERIALIZED VIEW":
            # Get materialized view definition
            sql = f"""
                SELECT definition
                FROM pg_matviews
                WHERE schemaname = '{safe_schema}' AND matviewname = '{safe_table}'
            """
            result = self._execute_pandas(sql, database_name=database_name)
            if not result.empty and result["definition"][0]:
                return f"CREATE MATERIALIZED VIEW {full_name} AS\n{result['definition'][0]}"
            return f"-- DDL not available for {full_name}"

        else:
            # For tables, reconstruct DDL from column info
            columns = self.get_schema(
                database_name=database_name,
                schema_name=schema_name,
                table_name=table_name,
            )
            if not columns:
                return f"-- DDL not available for {full_name}"

            col_defs = []
            pk_cols = []
            for col in columns:
                col_def = f"    {self.quote_identifier(col['name'])} {col['type']}"
                if not col.get("nullable", True):
                    col_def += " NOT NULL"
                if col.get("default_value"):
                    col_def += f" DEFAULT {col['default_value']}"
                col_defs.append(col_def)
                if col.get("pk"):
                    pk_cols.append(col["name"])

            ddl = f"CREATE TABLE {full_name} (\n"
            ddl += ",\n".join(col_defs)
            if pk_cols:
                pk_names = ", ".join(self.quote_identifier(c) for c in pk_cols)
                ddl += f",\n    PRIMARY KEY ({pk_names})"
            ddl += "\n);"
            return ddl

    def _get_objects_with_ddl(
        self,
        table_type: TABLE_TYPE = "table",
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        """
        Get metadata with DDL statements.

        Args:
            table_type: Type of object
            tables: Optional list of specific tables to retrieve
            catalog_name: Catalog name (unused)
            database_name: Database name
            schema_name: Schema name

        Returns:
            List of metadata dictionaries with DDL
        """
        result = []
        filter_tables = self._reset_filter_tables(tables, catalog_name, database_name, schema_name)

        object_type_map = {
            "table": "TABLE",
            "view": "VIEW",
            "mv": "MATERIALIZED VIEW",
        }
        object_type = object_type_map.get(table_type, "TABLE")

        for meta in self._get_metadata(table_type, catalog_name, database_name, schema_name):
            metadata_database = meta["database_name"]
            full_name = self.full_name(
                database_name=metadata_database,
                schema_name=meta["schema_name"],
                table_name=meta["table_name"],
            )

            # Skip if not in filter list
            if filter_tables and full_name not in filter_tables:
                continue

            # Get DDL
            try:
                # Keep the legacy three-argument _get_ddl dispatch compatible
                # with independently released PostgreSQL subclasses. The
                # ContextVar is task-local and makes the target database visible
                # to both the base implementation and subclass metadata queries.
                token = self._database_var.set(metadata_database)
                try:
                    ddl = self._get_ddl(
                        meta["schema_name"],
                        meta["table_name"],
                        object_type,
                    )
                finally:
                    self._database_var.reset(token)
            except Exception as e:
                logger.warning(f"Could not get DDL for {full_name}: {e}")
                ddl = f"-- DDL not available for {meta['table_name']}"

            meta["definition"] = ddl
            result.append(meta)

        return result

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
        return [
            self._qualify_name(meta, database_name, schema_name)
            for meta in self._get_metadata("table", catalog_name, database_name, schema_name)
        ]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of view names."""
        return [
            self._qualify_name(meta, database_name, schema_name)
            for meta in self._get_metadata("view", catalog_name, database_name, schema_name)
        ]

    @override
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """Get list of materialized view names."""
        return [
            self._qualify_name(meta, database_name, schema_name)
            for meta in self._get_metadata("mv", catalog_name, database_name, schema_name)
        ]

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Get tables with DDL statements."""
        return self._get_objects_with_ddl("table", tables, catalog_name, database_name, schema_name)

    @override
    def get_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """Get views with DDL statements."""
        return self._get_objects_with_ddl("view", None, catalog_name, database_name, schema_name)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Get table schema using INFORMATION_SCHEMA.

        Args:
            catalog_name: Catalog name (unused)
            database_name: Database name
            schema_name: Schema name
            table_name: Table name

        Returns:
            List of column information dictionaries
        """
        if not table_name:
            return []

        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name

        safe_db = database_name.replace("'", "''") if database_name else ""
        safe_schema = schema_name.replace("'", "''") if schema_name else ""
        safe_table = table_name.replace("'", "''") if table_name else ""

        def _build_sql(case_insensitive: bool) -> str:
            if case_insensitive:
                schema_cmp = f"lower(c.table_schema) = lower('{safe_schema}')"
                table_cmp = f"lower(c.table_name) = lower('{safe_table}')"
                pk_schema_cmp = f"lower(tc.table_schema) = lower('{safe_schema}')"
                pk_table_cmp = f"lower(tc.table_name) = lower('{safe_table}')"
            else:
                schema_cmp = f"c.table_schema = '{safe_schema}'"
                table_cmp = f"c.table_name = '{safe_table}'"
                pk_schema_cmp = f"tc.table_schema = '{safe_schema}'"
                pk_table_cmp = f"tc.table_name = '{safe_table}'"
            return f"""
                SELECT
                    c.table_schema as table_schema,
                    c.table_name as table_name,
                    c.column_name as field,
                    c.data_type as type,
                    c.is_nullable as nullable,
                    c.column_default as default_value,
                    CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END as is_pk,
                    pgd.description as comment
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT kcu.table_schema, kcu.table_name, kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                        AND {pk_schema_cmp}
                        AND {pk_table_cmp}
                ) pk ON pk.table_schema = c.table_schema
                    AND pk.table_name = c.table_name
                    AND pk.column_name = c.column_name
                LEFT JOIN pg_catalog.pg_statio_all_tables st
                    ON st.schemaname = c.table_schema AND st.relname = c.table_name
                LEFT JOIN pg_catalog.pg_description pgd
                    ON pgd.objoid = st.relid AND pgd.objsubid = c.ordinal_position
                WHERE c.table_catalog = '{safe_db}'
                  AND {schema_cmp}
                  AND {table_cmp}
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
            """

        query_result = self._execute_pandas(_build_sql(False), database_name=database_name)
        if len(query_result) == 0:
            query_result = self._execute_pandas(_build_sql(True), database_name=database_name)
            self._raise_if_ambiguous_table(query_result, schema_name, table_name)

        result = []
        for i in range(len(query_result)):
            result.append(
                {
                    "cid": i,
                    "name": query_result["field"][i],
                    "type": query_result["type"][i],
                    "nullable": query_result["nullable"][i] == "YES",
                    "default_value": query_result["default_value"][i],
                    "pk": bool(query_result["is_pk"][i]),
                    "comment": query_result["comment"][i] if query_result["comment"][i] else None,
                }
            )
        return result

    # ==================== Case-insensitive Fallback Helpers ====================

    @staticmethod
    def _raise_if_ambiguous_schema(query_result, schema_name: str) -> None:
        """Raise if a case-insensitive schema fallback matches more than one stored schema name.

        Why: PostgreSQL stores identifiers in their literal case in information_schema. When a
        case-insensitive fallback finds e.g. both ``Foo`` and ``foo`` schemas, returning their
        union would silently mix unrelated objects.
        """
        if len(query_result) == 0:
            return
        distinct = set(query_result["table_schema"].unique())
        if len(distinct) > 1:
            raise DatusDbException(
                ErrorCode.COMMON_FIELD_INVALID,
                f"Ambiguous schema_name '{schema_name}': case-insensitive match resolves to "
                f"multiple stored schemas {sorted(distinct)}. Pass the exact (case-sensitive) name.",
            )

    @staticmethod
    def _raise_if_ambiguous_table(query_result, schema_name: str, table_name: str) -> None:
        """Raise if a case-insensitive table fallback matches more than one stored (schema,table)."""
        if len(query_result) == 0:
            return
        distinct = set(zip(query_result["table_schema"], query_result["table_name"]))
        if len(distinct) > 1:
            raise DatusDbException(
                ErrorCode.COMMON_FIELD_INVALID,
                f"Ambiguous table '{schema_name}.{table_name}': case-insensitive match resolves to "
                f"multiple stored tables {sorted(distinct)}. Pass the exact (case-sensitive) name.",
            )

    # ==================== Database/Schema Management ====================

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of databases."""
        sql = "SELECT datname FROM pg_database WHERE datistemplate = false"
        result = self._execute_pandas(sql)
        databases = result["datname"].tolist()

        if not include_sys:
            sys_dbs = self._sys_databases()
            databases = [db for db in databases if db not in sys_dbs]

        return databases

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of schemas in the current database."""
        database_name = database_name or self.database_name
        safe_db = database_name.replace("'", "''") if database_name else ""
        sql = f"SELECT schema_name FROM information_schema.schemata WHERE catalog_name = '{safe_db}'"
        result = self._execute_pandas(sql, database_name=database_name)
        schemas = result["schema_name"].tolist()

        if not include_sys:
            sys_schemas = self._sys_schemas()
            schemas = [s for s in schemas if s not in sys_schemas]

        return schemas

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        """Get schema name for SQLAlchemy Inspector."""
        return schema_name or self.schema_name

    def _get_engine(self, database_name: str = ""):
        """Get or create engine for the given database. Thread-safe.

        PostgreSQL requires different connection strings per database,
        so each database gets its own engine with connection pool.
        Uses LRU eviction (max 8 engines) to avoid holding too many connections.
        """
        db = database_name or self.database_name
        with self._engine_lock:
            if db in self._engines:
                self._engines.move_to_end(db)
                return self._engines[db]
            conn_str = self._build_connection_string(db)
            engine = create_engine(
                conn_str,
                pool_size=5,
                max_overflow=10,
                pool_timeout=self.timeout_seconds,
                pool_recycle=3600,
                pool_pre_ping=True,
            )
            self._engines[db] = engine
            while len(self._engines) > self._max_engines:
                _, evicted = self._engines.popitem(last=False)
                try:
                    evicted.dispose()
                except Exception as e:
                    logger.warning(f"Error disposing evicted engine: {e}")
            return engine

    @override
    def _conn(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Checkout connection from the correct per-database engine. Thread-safe.

        Overrides base _conn() to avoid writing to shared self.engine.
        Each thread gets a connection from the engine matching its database_name.
        """
        from contextlib import contextmanager

        @contextmanager
        def _pg_conn():
            effective_database = database_name or self.database_name
            effective_schema = schema_name or self.schema_name
            effective_catalog = catalog_name or self.catalog_name
            engine = self._get_engine(effective_database)
            conn = engine.connect()
            try:
                self.do_switch_context(conn, effective_catalog, effective_database, effective_schema)
                yield conn
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

        return _pg_conn()

    @override
    def close(self):
        """Dispose all engines (per-database pool + parent engine)."""
        for engine in self._engines.values():
            try:
                engine.dispose()
            except Exception as e:
                logger.warning(f"Error disposing engine: {e}")
        self._engines.clear()
        # Dispose parent engine that may have been created via connect()/_ensure_engine()
        super().close()

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply schema context to a connection.

        Database switching is handled by _conn() which picks the right engine
        based on the effective database_name.
        """
        if schema_name:
            conn.execute(text(f"SET search_path TO {self.quote_identifier(schema_name)}"))
            conn.commit()

    # ==================== Sample Data ====================

    def get_sample_rows(
        self,
        tables: Optional[List[str]] = None,
        top_n: int = 5,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_type: TABLE_TYPE = "table",
    ) -> List[Dict[str, str]]:
        """Get sample rows from tables."""
        # Delegate to base class for unsupported table types (e.g., "full")
        if table_type == "full" or table_type not in METADATA_DICT:
            return super().get_sample_rows(
                tables=tables,
                top_n=top_n,
                catalog_name=catalog_name,
                database_name=database_name,
                schema_name=schema_name,
                table_type=table_type,
            )

        self.connect()
        schema_name = schema_name or self.schema_name
        result = []

        # If specific tables provided, query those
        if tables:
            for table_name in tables:
                full_name = self.full_name(
                    database_name=database_name,
                    schema_name=schema_name,
                    table_name=table_name,
                )
                sql = f"SELECT * FROM {full_name} LIMIT {top_n}"
                df = self._execute_pandas(sql, database_name=database_name)
                if not df.empty:
                    result.append(
                        {
                            "identifier": self.identifier(
                                database_name=database_name,
                                schema_name=schema_name,
                                table_name=table_name,
                            ),
                            "catalog_name": "",
                            "database_name": database_name or self.database_name,
                            "schema_name": schema_name,
                            "table_name": table_name,
                            "sample_rows": df.to_csv(index=False),
                        }
                    )
            return result

        # Otherwise get metadata and query all tables
        metadata = self._get_metadata(table_type, "", database_name, schema_name)
        for meta in metadata:
            metadata_database = meta["database_name"]
            full_name = self.full_name(
                database_name=metadata_database,
                schema_name=meta["schema_name"],
                table_name=meta["table_name"],
            )
            sql = f"SELECT * FROM {full_name} LIMIT {top_n}"
            df = self._execute_pandas(sql, database_name=metadata_database)
            if not df.empty:
                result.append(
                    {
                        "identifier": meta["identifier"],
                        "catalog_name": "",
                        "database_name": metadata_database,
                        "schema_name": meta["schema_name"],
                        "table_name": meta["table_name"],
                        "sample_rows": df.to_csv(index=False),
                    }
                )
        return result

    # ==================== Utility Methods ====================

    @override
    def identifier(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Generate a unique identifier for a table."""
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name
        if database_name and schema_name:
            return f"{database_name}.{schema_name}.{table_name}"
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """Build fully-qualified table name."""
        database_name = database_name or self.database_name
        schema_name = schema_name or self.schema_name
        if database_name and schema_name:
            return f"{self.quote_identifier(database_name)}.{self.quote_identifier(schema_name)}.{self.quote_identifier(table_name)}"
        if schema_name:
            return f"{self.quote_identifier(schema_name)}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    @override
    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        """Reset filter tables with full names."""
        schema_name = schema_name or self.schema_name
        return super()._reset_filter_tables(tables, "", database_name, schema_name)

    # ==================== MigrationTargetMixin ====================

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "postgres-like",
            "requires": [],  # OLTP — no distribution/partition required
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks-only)",
                "ENGINE = (MySQL/ClickHouse syntax)",
            ],
            "type_hints": {
                "HUGEINT": "NUMERIC(38,0) (Postgres has no HUGEINT/LARGEINT)",
                "LARGEINT": "NUMERIC(38,0)",
                "unbounded VARCHAR": "TEXT (prefer TEXT over unbounded VARCHAR)",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
                "JSON": "JSONB (prefer for indexing)",
                "BOOLEAN": "BOOLEAN (no TINYINT cast needed)",
            },
            "example_ddl": (
                "CREATE TABLE public.t (\n"
                "  id BIGSERIAL PRIMARY KEY,\n"
                "  name VARCHAR(255),\n"
                "  created_at TIMESTAMPTZ DEFAULT now()\n"
                ")"
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Postgres is OLTP — no distribution keys or bucketing required
        return {}

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()

        if "DUPLICATE KEY" in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; Postgres does not support it")
        if "BUCKETS" in upper and "DISTRIBUTED BY" in upper:
            errors.append("DISTRIBUTED BY ... BUCKETS is StarRocks syntax; Postgres does not support it")
        if "ENGINE =" in upper or "ENGINE=" in upper:
            errors.append("ENGINE clause is MySQL/ClickHouse syntax; not supported in Postgres")
        if "ORDER BY" in upper and "CREATE TABLE" in upper:
            # Rough heuristic: top-level ORDER BY inside CREATE TABLE is ClickHouse's
            # MergeTree syntax. Postgres allows ORDER BY inside CTAS SELECT, so this
            # check is intentionally loose (only flags when accompanied by ENGINE).
            if "ENGINE" in upper:
                errors.append("ORDER BY inside CREATE TABLE is ClickHouse syntax; use CREATE INDEX in Postgres")

        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "NUMERIC(38,0)",
            "LARGEINT": "NUMERIC(38,0)",
            "DATETIME": "TIMESTAMP",
        }
        return overrides.get(base)
