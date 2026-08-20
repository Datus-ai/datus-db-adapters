# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from contextlib import contextmanager
from typing import Any, Dict, List, Literal, Optional, Set, Union, override

from sqlalchemy import text

from datus_db_core import (
    TABLE_TYPE,
    BaseSqlConnector,
    CatalogSupportMixin,
    ExecuteSQLResult,
    MaterializedViewSupportMixin,
    MigrationTargetMixin,
    get_logger,
    list_to_in_str,
    parse_context_switch,
)
from datus_mysql import MySQLConnector

from .config import DorisConfig
from .handlers import parse_doris_identifier

logger = get_logger(__name__)

_INTEGER_TYPES = frozenset({"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "INT2", "INT4", "INT8", "LARGEINT"})
_SWITCH_RE = re.compile(r"^\s*SWITCH\s+(.+?)\s*;?\s*$", flags=re.IGNORECASE | re.DOTALL)

# ``information_schema.COLUMNS.COLUMN_KEY`` values Doris emits for key columns.
# Sourced from ``KeysType.toMetadata()``; a non-key column reports an empty string.
_COLUMN_KEY_VALUES = frozenset({"PRI", "DUP", "UNI", "AGG"})
_UNIQUE_COLUMN_KEY_VALUES = frozenset({"PRI", "UNI"})

# Types Doris rejects as OLAP key columns (ColumnDefinition.validate).
_NON_KEY_TYPES = frozenset(
    {"FLOAT", "DOUBLE", "STRING", "TEXT", "JSON", "JSONB", "VARIANT", "ARRAY", "MAP", "STRUCT", "BITMAP", "HLL"}
)


def _parse_doris_context_switch(sql: str) -> Optional[Dict[str, Any]]:
    """Parse the Doris context commands ``SWITCH <catalog>`` and ``USE [<catalog>.]<database>``.

    ``SWITCH`` is matched locally first: a SQL parser that does not know the
    keyword can read ``SWITCH internal`` as an expression with an alias instead
    of a context command, which would silently drop the catalog change.
    """
    switch_match = _SWITCH_RE.match(sql)
    if switch_match:
        target = switch_match.group(1).rstrip(";").strip()
        # Reuse the USE parser to unwrap quoting on the catalog identifier. A
        # target the parser cannot tokenize is reported as "not a context
        # command" rather than raised: this only tracks connector state, and
        # the statement's real outcome already came back from the server.
        try:
            parsed_target = parse_context_switch(f"USE {target}", dialect="doris")
        except Exception as e:  # noqa: BLE001 - malformed target, not a context switch
            logger.debug(f"Could not parse SWITCH target {target!r}: {e}")
            return None
        if not parsed_target:
            return None
        catalog_name = parsed_target.get("database_name") or parsed_target.get("catalog_name")
        return {
            "command": "SWITCH",
            "target": "catalog",
            "catalog_name": catalog_name,
            "database_name": "",
            "schema_name": "",
        }

    return parse_context_switch(sql, dialect="doris")


class DorisConnector(MySQLConnector, CatalogSupportMixin, MaterializedViewSupportMixin, MigrationTargetMixin):
    """
    Doris database connector.

    Doris uses MySQL protocol but adds multi-catalog support and materialized views.
    Metadata queries use catalog-qualified information_schema (e.g.
    `catalog.information_schema.TABLES`) so they are stateless and thread-safe,
    without requiring session context switching.
    """

    def __init__(self, config: Union[DorisConfig, dict]):
        """
        Initialize Doris connector.

        Args:
            config: DorisConfig object or dict with configuration
        """
        # Handle config object or dict
        if isinstance(config, dict):
            config = DorisConfig(**config)
        elif not isinstance(config, DorisConfig):
            raise TypeError(f"config must be DorisConfig or dict, got {type(config)}")

        self.doris_config = config

        # Pass MySQL config to parent connector
        from datus_mysql import MySQLConfig

        # When using a non-default catalog, don't put database in the connection
        # string — it would fail because the database doesn't exist under
        # internal.  We switch catalog and USE database in connect().
        needs_catalog_switch = config.catalog and config.catalog not in ("internal", "def")
        mysql_database = "" if needs_catalog_switch else (config.database or "")

        mysql_config = MySQLConfig(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            database=mysql_database,
            charset=config.charset,
            autocommit=config.autocommit,
            timeout_seconds=config.timeout_seconds,
        )
        super().__init__(mysql_config)
        self._default_catalog = self.default_catalog() if config.catalog in ("", None, "def") else config.catalog
        # Keep the configured database in connector context even when it must
        # be omitted from the initial MySQL connection URL. _conn() consumes
        # this value after switching to the configured external catalog.
        self._default_database = config.database or ""

        # Override dialect to Doris
        self.dialect = "doris"

    # ==================== Context Manager Support ====================

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        return False  # Don't suppress exceptions

    @override
    def execute(
        self,
        input_params: Any,
        result_format: Optional[Literal["csv", "arrow", "pandas", "list"]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        """Route Doris SWITCH commands even with older core SQL classifiers."""
        sql_query = (
            input_params.get("sql_query")
            if isinstance(input_params, dict)
            else getattr(input_params, "sql_query", None)
        )
        if isinstance(sql_query, str) and _SWITCH_RE.match(sql_query):
            self.validate_input(input_params)
            return self.execute_content_set(sql_query.strip())
        return super().execute(
            input_params=input_params,
            result_format=result_format,
            catalog_name=catalog_name,
            database_name=database_name,
            schema_name=schema_name,
        )

    # ==================== Catalog Management (CatalogSupportMixin) ====================

    @override
    def default_catalog(self) -> str:
        """Doris default catalog."""
        return "internal"

    @override
    def get_catalogs(self) -> List[str]:
        """Get list of catalogs."""
        result = self._execute_pandas("SHOW CATALOGS")
        if result.empty:
            return []
        catalog_column = "CatalogName" if "CatalogName" in result.columns else "Catalog"
        return result[catalog_column].tolist()

    @override
    def switch_catalog(self, catalog_name: str) -> None:
        """Switch to a different catalog.

        Clears database_name because the old database may not exist
        in the new catalog.

        Args:
            catalog_name: Name of the catalog to switch to
        """
        self.switch_context(catalog_name=catalog_name)
        self.database_name = ""

    def _resolve_catalog(self, catalog_name: str = "") -> str:
        """Resolve the effective catalog name, falling back to configured or default."""
        catalog = catalog_name or self.catalog_name
        if not catalog or catalog == "def":
            return self.default_catalog()
        return catalog

    @override
    def get_current_context(self) -> Dict[str, str]:
        """Return Doris SQL coordinates with the effective catalog resolved."""
        return {
            "catalog_name": self._resolve_catalog(),
            "database_name": self.database_name or "",
            "schema_name": "",
        }

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply catalog and/or database context to a connection."""
        if catalog_name:
            conn.execute(text(f"SWITCH {self.quote_identifier(catalog_name)}"))
            conn.commit()
            logger.debug(f"Switched catalog to: {catalog_name}")
        if database_name:
            conn.execute(text(f"USE {self.quote_identifier(database_name)}"))
            conn.commit()
            logger.debug(f"Switched database to: {database_name}")

    @contextmanager
    @override
    def _conn(self, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Checkout a connection with catalog-aware context.

        When a per-call ``catalog_name`` override targets a catalog different
        from the stored thread-local catalog and no ``database_name`` is
        passed explicitly, the stored ``self.database_name`` is NOT carried
        over: it belongs to the old catalog and may not exist in the new
        one, which would fail ``USE <db>`` after ``SWITCH <new>``.

        The per-call ``catalog_name`` is normalized via ``_resolve_catalog``
        (e.g. the ``"def"`` alias → ``internal``) before both the
        divergence check and the downstream ``SWITCH``, so aliases are
        compared and applied consistently.
        """
        resolved_catalog = self._resolve_catalog(catalog_name) if catalog_name else ""
        if resolved_catalog and not database_name and resolved_catalog != self.catalog_name:
            effective_database = ""
            effective_schema = schema_name or self.schema_name
            engine = self._ensure_engine()
            conn = engine.connect()
            try:
                self.do_switch_context(
                    conn,
                    catalog_name=resolved_catalog,
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
        else:
            with super()._conn(
                catalog_name=resolved_catalog or catalog_name,
                database_name=database_name,
                schema_name=schema_name,
            ) as conn:
                yield conn

    @override
    def execute_content_set(self, sql: str) -> ExecuteSQLResult:
        """Execute USE/SWITCH, clearing stored database on catalog changes.

        Mirrors ``switch_catalog()``: when the catalog changes, the stored
        ``database_name`` no longer refers to a valid database under the
        new catalog, so it is cleared to avoid a stale ``USE`` on the next
        checkout.
        """
        result = super().execute_content_set(sql)
        if result.success:
            context = _parse_doris_context_switch(sql)
            if context:
                if catalog := context.get("catalog_name"):
                    self.catalog_name = catalog
                if database := context.get("database_name"):
                    self.database_name = database
                if context.get("target") == "catalog" and context.get("catalog_name"):
                    self.database_name = ""
        return result

    # ==================== Metadata Retrieval (Stateless, Catalog-Qualified) ====================

    def _get_metadata(
        self,
        table_type: str = "table",
        catalog_name: str = "",
        database_name: str = "",
    ) -> List[Dict[str, str]]:
        """
        Get metadata for tables/views using catalog-qualified information_schema.

        Uses `catalog.information_schema.TABLES` syntax so no session-level
        catalog switch is needed.
        This makes metadata queries stateless and thread-safe.
        """
        self.connect()
        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name

        if table_type == "mv":
            return self._get_materialized_view_metadata(current_catalog, database_name)

        from datus_mysql.connector import _get_metadata_config

        metadata_config = _get_metadata_config(table_type)

        # Build WHERE clause
        if database_name:
            safe_db = database_name.replace("\\", "\\\\").replace("'", "''")
            where = f"TABLE_SCHEMA = '{safe_db}'"
        else:
            where = list_to_in_str("TABLE_SCHEMA NOT IN", list(self._sys_databases()))

        type_filter = list_to_in_str("AND TABLE_TYPE IN", metadata_config.table_types)

        # Use catalog-qualified information_schema without changing session context.
        query = (
            f"SELECT TABLE_SCHEMA, TABLE_NAME "
            f"FROM {self.quote_identifier(current_catalog)}.information_schema.{metadata_config.info_table} "
            f"WHERE {where} {type_filter}"
        )

        query_result = self._execute_pandas(query)

        result = []
        for i in range(len(query_result)):
            db_name = query_result["TABLE_SCHEMA"][i]
            tb_name = query_result["TABLE_NAME"][i]
            result.append(
                {
                    "identifier": self.identifier(
                        catalog_name=current_catalog, database_name=db_name, table_name=tb_name
                    ),
                    "catalog_name": current_catalog,
                    "schema_name": "",
                    "database_name": db_name,
                    "table_name": tb_name,
                    "table_type": table_type,
                }
            )
        if table_type == "table" and current_catalog == self.default_catalog():
            materialized_views = {
                (mv["database_name"], mv["table_name"])
                for mv in self._get_materialized_view_metadata(current_catalog, database_name)
            }
            result = [meta for meta in result if (meta["database_name"], meta["table_name"]) not in materialized_views]
        return result

    def _get_materialized_view_metadata(self, catalog_name: str, database_name: str = "") -> List[Dict[str, str]]:
        """Return independently queryable Doris asynchronous materialized views."""
        if catalog_name != self.default_catalog():
            return []

        databases = [database_name] if database_name else self.get_databases(catalog_name=catalog_name)
        result: List[Dict[str, str]] = []
        for db_name in databases:
            safe_db = db_name.replace("\\", "\\\\").replace('"', '\\"')
            rows = self._execute_pandas(f'SELECT Name, QuerySql FROM mv_infos("database"="{safe_db}")')
            for i in range(len(rows)):
                table_name = str(rows["Name"][i])
                result.append(
                    {
                        "identifier": self.identifier(
                            catalog_name=catalog_name,
                            database_name=db_name,
                            table_name=table_name,
                        ),
                        "catalog_name": catalog_name,
                        "schema_name": "",
                        "database_name": db_name,
                        "table_name": table_name,
                        "table_type": "mv",
                        "query_sql": str(rows["QuerySql"][i]),
                    }
                )
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
        result = self._get_metadata(table_type="table", catalog_name=catalog_name, database_name=database_name)
        return [self._qualify_name(table, database_name, schema_name) for table in result]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of view names."""
        try:
            result = self._get_metadata(table_type="view", catalog_name=catalog_name, database_name=database_name)
            return [self._qualify_name(view, database_name, schema_name) for view in result]
        except Exception as e:
            logger.warning(f"Failed to get views: {e}")
            return []

    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """Get list of materialized view names."""
        try:
            result = self._get_metadata(table_type="mv", catalog_name=catalog_name, database_name=database_name)
            return [self._qualify_name(mv, database_name, schema_name) for mv in result]
        except Exception as e:
            logger.warning(f"Failed to get materialized views: {e}")
            return []

    def get_materialized_views_with_ddl(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[Dict[str, str]]:
        """Get asynchronous materialized views with their Doris DDL definitions."""
        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name
        result = []
        for meta in self._get_materialized_view_metadata(current_catalog, database_name):
            full_name = self.full_name(
                catalog_name=meta["catalog_name"],
                database_name=meta["database_name"],
                table_name=meta["table_name"],
            )
            try:
                definition = self._show_create(full_name, "MATERIALIZED VIEW")
            except Exception as e:
                logger.warning(f"Could not get DDL for {full_name}: {e}")
                definition = meta["query_sql"]
            meta["definition"] = definition
            meta.pop("query_sql", None)
            result.append(meta)
        return result

    @override
    def _get_objects_with_ddl(
        self,
        table_type: TABLE_TYPE = "table",
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
    ) -> List[Dict[str, str]]:
        """Get catalog-aware Doris table or view metadata with DDL."""
        from datus_mysql.connector import _get_metadata_config

        requested = set(tables or [])
        metadata_config = _get_metadata_config(table_type)
        result = []
        for meta in self._get_metadata(table_type, catalog_name, database_name):
            full_name = self.full_name(
                catalog_name=meta["catalog_name"],
                database_name=meta["database_name"],
                table_name=meta["table_name"],
            )
            if requested and meta["table_name"] not in requested and full_name not in requested:
                continue
            try:
                meta["definition"] = self._show_create(full_name, metadata_config.show_create_table)
            except Exception as e:
                logger.warning(f"Could not get DDL for {full_name}: {e}")
                meta["definition"] = f"-- DDL not available for {meta['table_name']}"
            result.append(meta)
        return result

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get Doris column metadata from the requested catalog."""
        if not table_name:
            return []
        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name
        safe_db = database_name.replace("\\", "\\\\").replace("'", "''")
        safe_table = table_name.replace("\\", "\\\\").replace("'", "''")
        rows = self._execute_pandas(
            f"""
            SELECT
                COLUMN_NAME AS Field,
                COLUMN_TYPE AS Type,
                IS_NULLABLE AS `Null`,
                COLUMN_KEY AS `Key`,
                COLUMN_DEFAULT AS `Default`,
                COLUMN_COMMENT AS Comment
            FROM {self.quote_identifier(current_catalog)}.information_schema.columns
            WHERE TABLE_SCHEMA = '{safe_db}'
              AND TABLE_NAME = '{safe_table}'
            ORDER BY ORDINAL_POSITION
            """
        )
        return [
            {
                "cid": i,
                "name": rows["Field"][i],
                "type": rows["Type"][i],
                "nullable": rows["Null"][i] == "YES",
                "default_value": rows["Default"][i],
                "pk": self._is_unique_key_column(rows["Key"][i]),
                "key": bool(self._normalize_column_key(rows["Key"][i])),
                "key_type": self._normalize_column_key(rows["Key"][i]),
                "comment": rows["Comment"][i],
            }
            for i in range(len(rows))
        ]

    @staticmethod
    def _normalize_column_key(value: Any) -> str:
        """Normalize ``information_schema.COLUMNS.COLUMN_KEY`` for a Doris column.

        Doris fills this from ``KeysType.toMetadata()`` for every key column, so
        the value is the *table model* rather than MySQL's per-column key flag:
        ``DUP`` (Duplicate Key), ``UNI`` (Unique Key), ``AGG`` (Aggregate Key),
        or ``PRI``. Non-key columns get an empty string.
        """
        if value is None:
            return ""
        text = str(value).strip().upper()
        return text if text in _COLUMN_KEY_VALUES else ""

    @classmethod
    def _is_unique_key_column(cls, value: Any) -> bool:
        """Report whether a key column carries unique-row semantics.

        Only Unique Key (and the internal Primary Key model) identify a row.
        Duplicate Key and Aggregate Key columns are sort/group keys that permit
        repeated values, so reporting them as primary keys would mislead callers
        that build joins or upserts from the schema.
        """
        return cls._normalize_column_key(value) in _UNIQUE_COLUMN_KEY_VALUES

    # ==================== Database Management ====================

    def _sys_databases(self) -> Set[str]:
        """System databases to filter out (Doris-specific)."""
        # Include MySQL system databases plus Doris-specific ones
        mysql_sys = super()._sys_databases()
        doris_sys = {"__internal_schema"}
        return mysql_sys | doris_sys

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of databases using catalog-qualified SHOW DATABASES."""
        current_catalog = self._resolve_catalog(catalog_name)
        result = self._execute_pandas(f"SHOW DATABASES FROM {self.quote_identifier(current_catalog)}")
        if result.empty:
            return []
        databases = result.iloc[:, 0].tolist()
        if not include_sys:
            sys_dbs = self._sys_databases()
            databases = [db for db in databases if db not in sys_dbs]
        return databases

    # ==================== Full Name Construction ====================

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """
        Build fully-qualified table name with catalog support.

        Doris format: `catalog`.`database`.`table`
        """
        catalog_name = self._resolve_catalog(catalog_name)
        quoted_table = self.quote_identifier(table_name)

        if catalog_name:
            if database_name:
                return ".".join(
                    (
                        self.quote_identifier(catalog_name),
                        self.quote_identifier(database_name),
                        quoted_table,
                    )
                )
            return quoted_table
        if database_name:
            return f"{self.quote_identifier(database_name)}.{quoted_table}"
        return quoted_table

    @override
    def _reset_filter_tables(
        self,
        tables: Optional[List[str]] = None,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[str]:
        """Expand a table filter to fully-qualified names, keeping the catalog.

        The MySQL base drops ``catalog_name`` before delegating, which would
        silently resolve an explicitly requested catalog back to the connector's
        current one.
        """
        database_name = database_name or self.database_name
        return BaseSqlConnector._reset_filter_tables(
            self, tables, self._resolve_catalog(catalog_name), database_name, ""
        )

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        """Get schema name for SQLAlchemy Inspector with catalog support."""
        database_name = database_name or self.database_name

        catalog_name = catalog_name or self.catalog_name or self.default_catalog()
        if database_name:
            return f"{catalog_name}.{database_name}"
        return None

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
        """Get sample rows without losing the requested Doris catalog."""
        if table_type == "full":
            return super().get_sample_rows(
                tables=tables,
                top_n=top_n,
                catalog_name=catalog_name,
                database_name=database_name,
                schema_name=schema_name,
                table_type=table_type,
            )

        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name
        requested = set(tables or [])
        result = []
        for meta in self._get_metadata(table_type, current_catalog, database_name):
            qualified_name = self._qualify_name(meta, database_name, schema_name)
            full_name = self.full_name(
                catalog_name=meta["catalog_name"],
                database_name=meta["database_name"],
                table_name=meta["table_name"],
            )
            if (
                requested
                and meta["table_name"] not in requested
                and qualified_name not in requested
                and full_name not in requested
            ):
                continue
            rows = self._execute_pandas(f"SELECT * FROM {full_name} LIMIT {top_n}")
            if rows.empty:
                continue
            result.append({**meta, "sample_rows": rows.to_csv(index=False)})
        return result

    # ==================== Connection Cleanup ====================

    @override
    def close(self):
        """
        Close engine with special handling for PyMySQL cleanup errors.

        Doris may trigger PyMySQL struct.pack errors during cleanup,
        which we safely ignore.
        """
        try:
            super().close()
        except Exception as e:
            error_str = str(e)

            # Check for known PyMySQL cleanup errors
            pymysql_errors = [
                "struct.error",
                "struct.pack",
                "COMMAND.COM_QUIT",
                "required argument is not an integer",
            ]

            if any(err in error_str for err in pymysql_errors):
                logger.debug(f"Ignoring PyMySQL cleanup error: {e}")
                if hasattr(self, "engine"):
                    try:
                        if self.engine:
                            self.engine.dispose()
                    except Exception:
                        pass
                    finally:
                        self.engine = None
                self._owns_engine = False
            else:
                logger.error(f"Unexpected close error: {e}")
                raise

    # ==================== Utility Methods ====================

    def to_dict(self) -> Dict[str, Any]:
        """Convert connector to serializable dictionary."""
        return {
            "db_type": "doris",
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "catalog": self.catalog_name,
            "database": self.database_name,
        }

    def get_type(self) -> str:
        """Return the database type."""
        return "doris"

    @override
    def test_connection(self) -> bool:
        """Test the database connection with proper cleanup."""
        try:
            return super().test_connection()
        finally:
            # Ensure connection is closed after test
            try:
                self.close()
            except Exception as e:
                logger.debug(f"Ignoring cleanup error during test: {e}")

    # ==================== MigrationTargetMixin ====================

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        """Describe Doris-specific DDL and type-mapping requirements.

        ``requires`` is what ``validate_ddl`` enforces, so the two stay in sync.
        It is deliberately stricter than the engine: Doris accepts a table with
        no key clause and no distribution clause and derives both silently (see
        ``defaults_when_omitted``). A migration that takes those derived values
        lands a key model and bucket layout nobody chose, on a table nobody will
        re-examine, so both clauses must be written out explicitly here.
        """
        return {
            "supported": True,
            "dialect_family": "mysql-like",
            "requires": [
                "One of DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY",
                "DISTRIBUTED BY HASH(cols) BUCKETS N (or BUCKETS AUTO)",
            ],
            "recommends": [
                'PROPERTIES ("replication_num" = "N") sized to the cluster',
            ],
            "defaults_when_omitted": {
                "note": (
                    "Doris derives these when they are absent, but a migration must not rely on it: "
                    "validate_ddl rejects DDL that leaves either clause implicit"
                ),
                "key model": (
                    "AGGREGATE KEY when any column declares an aggregate function, otherwise "
                    "DUPLICATE KEY over a short-key prefix (at most 3 columns / 36 bytes)"
                ),
                "distribution": (
                    "DISTRIBUTED BY RANDOM with 10 buckets; a DISTRIBUTED BY clause that omits "
                    "BUCKETS takes 10 as well (FeConstants.default_bucket_num)"
                ),
            },
            "forbids": [
                "PRIMARY KEY as a table model (Doris uses UNIQUE KEY for upsert semantics)",
                "FOREIGN KEY",
                "FULLTEXT INDEX",
                "CHECK",
                "TIME columns (not supported for OLAP tables)",
                "FLOAT / DOUBLE / STRING / JSON / VARIANT / ARRAY / MAP / STRUCT as key columns",
                "AUTO_INCREMENT in an AGGREGATE KEY table",
                "DISTRIBUTED BY RANDOM in a UNIQUE KEY table",
            ],
            "type_hints": {
                "unbounded VARCHAR": "VARCHAR(65533) (or STRING when longer)",
                "CHAR(n)": "CHAR(n) up to 255, VARCHAR beyond that",
                "TEXT": "STRING (TEXT is accepted as an alias)",
                "TIMESTAMP": "DATETIME",
                "TIMESTAMPTZ": "DATETIME (TIMESTAMPTZ exists in Doris 4.x; verify the target version)",
                "TIME": "VARCHAR(20) (TIME columns are rejected on OLAP tables)",
                "UUID": "VARCHAR(36)",
                "HUGEINT": "LARGEINT",
                "INET / CIDR": (
                    "VARCHAR(43) — IPV4/IPV6 store a bare address, so a netmask or the wrong "
                    "address family is silently stored as NULL"
                ),
                "semi-structured JSON": "VARIANT for schema-on-read, JSON for opaque documents",
                "BYTEA / BLOB": "STRING (VARBINARY exists from Doris 4.0 but cannot be used in CREATE TABLE)",
            },
            "key_column_rules": [
                "Key columns come first, in declaration order",
                "FLOAT and DOUBLE cannot be key columns — use DECIMAL",
                "STRING / JSON / VARIANT and complex types cannot be key columns — use VARCHAR",
                "In an AGGREGATE KEY table every non-key column needs an aggregate function",
            ],
            "example_ddl": (
                "CREATE TABLE db.t (\n"
                "  id BIGINT NOT NULL,\n"
                "  name VARCHAR(255)\n"
                ") ENGINE=OLAP\n"
                "DUPLICATE KEY(id)\n"
                "DISTRIBUTED BY HASH(id) BUCKETS 10\n"
                'PROPERTIES ("replication_num" = "1")'
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Suggest Doris duplicate-key and distribution columns.

        The three-column ceiling and the ten-bucket default match what Doris
        itself derives when the clauses are omitted
        (``shortkey_max_column_count = 3``, ``default_bucket_num = 10``), so an
        explicit layout stays close to the engine's own choice.
        """
        if not columns:
            return {"duplicate_key": [], "distributed_by": [], "buckets": 10}

        keys = self._score_keys(columns, max_keys=3)
        return {"duplicate_key": keys, "distributed_by": keys, "buckets": 10}

    @staticmethod
    def _score_keys(columns: List[Dict[str, Any]], max_keys: int = 3) -> List[str]:
        """Select key columns using priority rules.

        Columns Doris rejects as keys (FLOAT, DOUBLE, STRING, JSON, VARIANT and
        the complex types) are excluded outright rather than scored, so the
        suggestion can always be written as a key clause.

        Priority among the remaining columns:
          1. Columns with 'id' or '_id' suffix (+100)
          2. INT/BIGINT type columns (+50)
          3. Non-nullable columns preferred (+10)
          4. Fallback to the first eligible column
        """
        eligible = []
        for col in columns:
            base_type = re.sub(r"\(.*\)", "", str(col.get("type", "")).upper()).strip()
            if base_type not in _NON_KEY_TYPES:
                eligible.append((col, base_type))
        if not eligible:
            return []

        scored = []
        for col, base_type in eligible:
            name = col["name"]
            nullable = col.get("nullable", True)

            score = 0
            if name.lower() == "id" or name.lower().endswith("_id"):
                score += 100
            if base_type in _INTEGER_TYPES:
                score += 50
            if not nullable:
                score += 10

            scored.append((score, name))

        scored.sort(key=lambda x: (-x[0], x[1]))

        if scored[0][0] == 0:
            return [eligible[0][0]["name"]]

        return [name for score, name in scored[:max_keys] if score > 0] or [eligible[0][0]["name"]]

    def validate_ddl(self, ddl: str) -> List[str]:
        """Return migration-blocking problems in a proposed Doris table DDL.

        Two classes of problem are reported together:

        * statements Doris itself rejects (unsupported clauses, key column
          types, AUTO_INCREMENT rules, RANDOM distribution on a unique table);
        * statements Doris accepts but a migration must not ship — a missing
          key clause or distribution clause, which Doris would silently derive.

        The second class is a deliberate policy: see
        ``describe_migration_capabilities()["defaults_when_omitted"]`` for what
        the engine would have chosen instead.
        """
        from datus_db_core.sql_utils import mask_sql_quoted_regions, strip_sql_comments

        errors: List[str] = []
        uncommented = strip_sql_comments(ddl).upper()
        upper = mask_sql_quoted_regions(uncommented)

        has_duplicate_key = bool(re.search(r"\bDUPLICATE\s+KEY\b", upper)) and not re.search(
            r"\bON\s+DUPLICATE\s+KEY\b", upper
        )
        has_unique_key = bool(re.search(r"\bUNIQUE\s+KEY\b", upper))
        has_aggregate_key = bool(re.search(r"\bAGGREGATE\s+KEY\b", upper))

        if not (has_duplicate_key or has_unique_key or has_aggregate_key):
            # PRIMARY KEY exists in Doris' internal KeysType enum, but CREATE
            # TABLE only accepts AGGREGATE | UNIQUE | DUPLICATE, so a statement
            # whose only key clause is PRIMARY KEY cannot be created at all.
            if re.search(r"\bPRIMARY\s+KEY\b", upper):
                errors.append(
                    "PRIMARY KEY is not a Doris table model; use UNIQUE KEY for upsert semantics, "
                    "or DUPLICATE KEY to retain detail rows"
                )
            else:
                errors.append(
                    "Doris DDL must define one of: DUPLICATE KEY / UNIQUE KEY / AGGREGATE KEY. "
                    "Doris would otherwise derive a key model from the column list, "
                    "which a migration must not leave implicit"
                )

        if not re.search(r"\bDISTRIBUTED\s+BY\b", upper):
            errors.append(
                "Doris DDL must include a DISTRIBUTED BY clause. "
                "Doris would otherwise default to random distribution with 10 buckets, "
                "which a migration must not leave implicit"
            )
        elif not re.search(r"\bBUCKETS\b", upper):
            # BUCKETS is optional in the grammar; omitting it takes
            # FeConstants.default_bucket_num, which is the same class of silent
            # default as an omitted DISTRIBUTED BY clause.
            errors.append(
                "Doris DDL must state a bucket count: BUCKETS N or BUCKETS AUTO. "
                "Doris would otherwise default to 10 buckets, "
                "which a migration must not leave implicit"
            )

        errors.extend(self._validate_auto_increment(upper, has_aggregate_key))
        errors.extend(self._validate_distribution(upper, has_unique_key))

        if re.search(r"\bFOREIGN\s+KEY\b", upper):
            errors.append("Doris does not support FOREIGN KEY")

        if re.search(r"\bFULLTEXT\b", upper):
            errors.append("Doris does not support FULLTEXT indexes")

        if re.search(r"\bCHECK\s*\(", upper):
            errors.append("Doris does not support CHECK constraints")

        # \bTIME\b cannot match inside TIMESTAMP/TIMESTAMPTZ, so no extra guard is
        # needed. A quoted column name is checked against the unmasked statement:
        # mask_sql_quoted_regions blanks it to whitespace, and inferring one back
        # out of that whitespace flags an indented column simply named "time".
        if re.search(r"[(,]\s*\w+\s+TIME\b", upper) or re.search(r"[(,]\s*[`\"][^`\"]+[`\"]\s+TIME\b", uncommented):
            errors.append("Doris does not support TIME columns on OLAP tables; use VARCHAR or DATETIME")

        errors.extend(self._validate_key_column_types(upper))
        return errors

    @staticmethod
    def _validate_auto_increment(upper: str, has_aggregate_key: bool) -> List[str]:
        """Check Doris' AUTO_INCREMENT rules.

        Doris allows AUTO_INCREMENT on Duplicate Key and Unique Key tables. The
        column must be a NOT NULL BIGINT without a default, and a table may
        carry at most one.
        """
        errors: List[str] = []
        matches = list(re.finditer(r"\bAUTO_INCREMENT\b", upper))
        if not matches:
            return errors

        if has_aggregate_key:
            errors.append("Doris AUTO_INCREMENT is only supported in DUPLICATE KEY and UNIQUE KEY tables")
        if len(matches) > 1:
            errors.append("Doris allows at most one AUTO_INCREMENT column per table")

        # Inspect the column definition the first marker sits in: everything
        # back to the preceding comma or opening parenthesis.
        start = max(upper.rfind(",", 0, matches[0].start()), upper.rfind("(", 0, matches[0].start()))
        column_def = upper[start + 1 : matches[0].end()]
        if not re.search(r"\bBIGINT\b", column_def):
            errors.append("Doris AUTO_INCREMENT columns must be BIGINT")
        # Doris columns are nullable unless NOT NULL is written out
        # (ColumnNullableType.DEFAULT), so absence of any keyword is a rejection
        # too: InternalCatalog.checkAutoIncColumns reports
        # ERR_AUTO_INCREMENT_COLUMN_NULLABLE on column.isAllowNull().
        if not re.search(r"\bNOT\s+NULL\b", column_def):
            errors.append("Doris AUTO_INCREMENT columns must be NOT NULL")
        if re.search(r"\bDEFAULT\b", column_def):
            errors.append("Doris AUTO_INCREMENT columns cannot have a DEFAULT value")
        return errors

    @staticmethod
    def _validate_distribution(upper: str, has_unique_key: bool) -> List[str]:
        """Check the distribution clause against the declared key model."""
        errors: List[str] = []
        if re.search(r"\bDISTRIBUTED\s+BY\s+RANDOM\b", upper) and has_unique_key:
            errors.append("Doris rejects DISTRIBUTED BY RANDOM on a UNIQUE KEY table; use DISTRIBUTED BY HASH")
        return errors

    @classmethod
    def _validate_key_column_types(cls, upper: str) -> List[str]:
        """Reject key columns whose declared type Doris forbids as a key.

        Only the columns named in the key clause are checked, and only when
        their type is declared in the same statement. ``upper`` has had its
        quoted regions blanked out, so a backtick-quoted column name is invisible
        here and is skipped rather than guessed at — the same trade-off the other
        checks make to stay free of false positives.
        """
        key_match = re.search(r"\b(?:DUPLICATE|UNIQUE|AGGREGATE)\s+KEY\s*\(([^)]*)\)", upper)
        if not key_match:
            return []

        key_columns = {part.strip() for part in key_match.group(1).split(",") if part.strip()}
        errors: List[str] = []
        for column in sorted(key_columns):
            declared = re.search(rf"[(,]\s*{re.escape(column)}\s+([A-Z0-9_]+)", upper)
            if declared and declared.group(1) in _NON_KEY_TYPES:
                errors.append(
                    f"Doris does not allow {declared.group(1)} column '{column.lower()}' as a key column; "
                    "use DECIMAL for FLOAT/DOUBLE or VARCHAR for STRING-like types"
                )
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> str | None:
        """Map a source type when Doris requires a dialect-specific override.

        ``TIMESTAMPTZ`` maps to ``DATETIME`` rather than Doris' own
        ``TIMESTAMPTZ``: the latter only exists from Doris 4.x, and this
        mapping has to hold for every version the adapter can connect to.

        Binary source types map to ``STRING`` rather than ``VARBINARY``.
        ``VARBINARY`` exists from Doris 4.0, but only as a type an external
        catalog can expose — ``CREATE TABLE`` rejects it, so a migration that
        took it as a hint would emit DDL no Doris version accepts.

        ``INET`` and ``CIDR`` map to ``VARCHAR(43)`` rather than to an IP type.
        ``IPV4`` accepts ``0.0.0.0``-``255.255.255.255`` and ``IPV6`` accepts a
        bare address; neither holds a netmask, and PostgreSQL ``cidr`` always
        carries one while ``inet`` may. Doris stores an out-of-range or
        malformed value as NULL rather than failing the load, so a narrower
        target would drop rows silently. 43 is the longest such value: a
        39-character IPv6 address plus ``/128``.
        """
        base_noparam = re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "LARGEINT",
            "TIMESTAMP": "DATETIME",
            "TIMESTAMPTZ": "DATETIME",
            "TIMESTAMP WITH TIME ZONE": "DATETIME",
            "TEXT": "STRING",
            "CLOB": "STRING",
            "TIME": "VARCHAR(20)",
            "UUID": "VARCHAR(36)",
            "BYTEA": "STRING",
            "BLOB": "STRING",
            "INET": "VARCHAR(43)",
            "CIDR": "VARCHAR(43)",
            "JSONB": "JSON",
            "UINT8": "SMALLINT",
            "UINT16": "INT",
            "UINT32": "BIGINT",
            "UINT64": "LARGEINT",
        }
        return overrides.get(base_noparam)

    @override
    def dry_run_ddl(self, ddl: str, target_table: str) -> List[str]:
        """Create the table under a temporary name, then drop it.

        The strongest validation available: Doris analyses the full statement,
        so derived key models, distribution defaults, and type restrictions are
        all exercised. ``target_table`` is replaced with a unique scratch name
        so an existing table is never touched, and the scratch table is dropped
        even when creation fails midway. Nothing is executed unless that
        replacement is visible in the statement being sent.
        """
        import uuid

        errors = self.validate_ddl(ddl)

        parsed = parse_doris_identifier(target_table)
        scratch_name = f"__datus_dry_run_{uuid.uuid4().hex[:12]}"
        scratch_full_name = self.full_name(
            catalog_name=parsed["catalog_name"],
            database_name=parsed["database_name"] or self.database_name,
            table_name=scratch_name,
        )

        original = self.full_name(
            catalog_name=parsed["catalog_name"],
            database_name=parsed["database_name"] or self.database_name,
            table_name=parsed["table_name"],
        )
        scratch_ddl = self._retarget_ddl(ddl, parsed["table_name"], original, scratch_full_name)
        if scratch_name not in scratch_ddl:
            # Nothing was rewritten, so executing the statement would run it
            # against whatever it already named. Report that instead: the text
            # checks above still stand, only the server-side pass is skipped.
            errors.append("Could not retarget the statement to a scratch table name; skipped the server-side dry run")
            return errors

        try:
            result = self.execute_ddl(scratch_ddl)
            if not result.success:
                errors.append(str(result.error))
        except Exception as e:  # noqa: BLE001 - surfaced as a validation error
            errors.append(str(e))
        finally:
            try:
                self.execute_ddl(f"DROP TABLE IF EXISTS {scratch_full_name}")
            except Exception as e:  # noqa: BLE001 - cleanup must not mask the result
                logger.warning(f"Could not drop dry-run table {scratch_full_name}: {e}")
        return errors

    @staticmethod
    def _retarget_ddl(ddl: str, table_name: str, original_full_name: str, scratch_full_name: str) -> str:
        """Point a CREATE TABLE statement at the scratch table name.

        The identifier after ``CREATE ... TABLE`` is rewritten first because it
        is the only rule anchored on the statement's actual target, and it
        already covers a differently-quoted or differently-qualified spelling. A
        substring replacement cannot take that role: the qualified name also
        appears in a leading comment or a ``COMMENT`` literal often enough, and
        rewriting that occurrence instead would leave the CREATE pointing at the
        real table.
        """
        pattern = re.compile(
            r"(CREATE\s+(?:EXTERNAL\s+|TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)([`\"\w.]+)",
            flags=re.IGNORECASE,
        )
        retargeted, count = pattern.subn(lambda m: f"{m.group(1)}{scratch_full_name}", ddl, count=1)
        if count:
            return retargeted
        # Not a CREATE TABLE statement. Fall back to the qualified name, then to
        # the bare name bounded as a whole identifier so a longer name that
        # merely contains it is left alone.
        if original_full_name and original_full_name in ddl:
            return ddl.replace(original_full_name, scratch_full_name, 1)
        if table_name:
            bounded = rf"(?<![\w`.]){re.escape(table_name)}(?![\w`])"
            retargeted, count = re.subn(bounded, lambda _m: scratch_full_name, ddl, count=1)
            if count:
                return retargeted
        return ddl
