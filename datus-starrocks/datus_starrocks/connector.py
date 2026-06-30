# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from contextlib import contextmanager
from typing import Any, Dict, List, Set, Union, override

from sqlalchemy import text

from datus_db_core import (
    CatalogSupportMixin,
    ExecuteSQLResult,
    MaterializedViewSupportMixin,
    MigrationTargetMixin,
    get_logger,
    list_to_in_str,
    parse_context_switch,
)
from datus_mysql import MySQLConnector

from .config import StarRocksConfig

logger = get_logger(__name__)

_INTEGER_TYPES = frozenset({"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "INT2", "INT4", "INT8", "LARGEINT"})


class StarRocksConnector(MySQLConnector, CatalogSupportMixin, MaterializedViewSupportMixin, MigrationTargetMixin):
    """
    StarRocks database connector.

    StarRocks uses MySQL protocol but adds multi-catalog support and materialized views.
    Metadata queries use catalog-qualified information_schema (e.g.
    `catalog.information_schema.TABLES`) so they are stateless and thread-safe,
    without requiring SET CATALOG context switching.
    """

    def __init__(self, config: Union[StarRocksConfig, dict]):
        """
        Initialize StarRocks connector.

        Args:
            config: StarRocksConfig object or dict with configuration
        """
        # Handle config object or dict
        if isinstance(config, dict):
            config = StarRocksConfig(**config)
        elif not isinstance(config, StarRocksConfig):
            raise TypeError(f"config must be StarRocksConfig or dict, got {type(config)}")

        self.starrocks_config = config

        # Pass MySQL config to parent connector
        from datus_mysql import MySQLConfig

        # When using a non-default catalog, don't put database in the connection
        # string — it would fail because the database doesn't exist under
        # default_catalog.  We switch catalog and USE database in connect().
        needs_catalog_switch = config.catalog and config.catalog not in ("default_catalog", "def")
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
        self._deferred_database = config.database if needs_catalog_switch else ""
        self._default_catalog = self.default_catalog() if config.catalog in ("", None, "def") else config.catalog
        self._default_database = config.database or ""

        # Override dialect to StarRocks
        self.dialect = "starrocks"

    # ==================== Context Manager Support ====================

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        return False  # Don't suppress exceptions

    # ==================== Catalog Management (CatalogSupportMixin) ====================

    @override
    def default_catalog(self) -> str:
        """StarRocks default catalog."""
        return "default_catalog"

    @override
    def get_catalogs(self) -> List[str]:
        """Get list of catalogs."""
        result = self._execute_pandas("SHOW CATALOGS")
        if result.empty:
            return []
        return result["Catalog"].tolist()

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
        """Return StarRocks SQL coordinates with the effective catalog resolved."""
        return {
            "catalog_name": self._resolve_catalog(),
            "database_name": self.database_name or "",
            "schema_name": "",
        }

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """Apply catalog and/or database context to a connection."""
        if catalog_name:
            conn.execute(text(f"SET CATALOG {self.quote_identifier(catalog_name)}"))
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
        one, which would fail ``USE <db>`` after ``SET CATALOG <new>``.

        The per-call ``catalog_name`` is normalized via ``_resolve_catalog``
        (e.g. the ``"def"`` alias → ``default_catalog``) before both the
        divergence check and the downstream ``SET CATALOG``, so aliases are
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
        """Execute USE/SET, clearing stored database on SET CATALOG.

        Mirrors ``switch_catalog()``: when the catalog changes, the stored
        ``database_name`` no longer refers to a valid database under the
        new catalog, so it is cleared to avoid a stale ``USE`` on the next
        checkout.
        """
        result = super().execute_content_set(sql)
        if result.success:
            context = parse_context_switch(sql=sql, dialect=self.dialect)
            if context and context.get("target") == "catalog" and context.get("catalog_name"):
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

        Uses `catalog.information_schema.TABLES` syntax so no SET CATALOG is needed.
        This makes metadata queries stateless and thread-safe.
        """
        self.connect()
        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name

        from datus_mysql.connector import _get_metadata_config

        metadata_config = _get_metadata_config(table_type)

        # Build WHERE clause
        if database_name:
            safe_db = database_name.replace("'", "''")
            where = f"TABLE_SCHEMA = '{safe_db}'"
        else:
            where = list_to_in_str("TABLE_SCHEMA NOT IN", list(self._sys_databases()))

        type_filter = list_to_in_str("AND TABLE_TYPE IN", metadata_config.table_types)

        # Use catalog-qualified information_schema — no SET CATALOG needed
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
        """
        Get materialized views with DDL definitions.

        Uses catalog-qualified information_schema query.
        """
        self.connect()
        current_catalog = self._resolve_catalog(catalog_name)
        database_name = database_name or self.database_name

        # Use catalog-qualified information_schema
        query_sql = (
            f"SELECT TABLE_SCHEMA, TABLE_NAME, MATERIALIZED_VIEW_DEFINITION "
            f"FROM {self.quote_identifier(current_catalog)}.information_schema.materialized_views"
        )

        if database_name:
            safe_db = database_name.replace("'", "''")
            query_sql = f"{query_sql} WHERE TABLE_SCHEMA = '{safe_db}'"
        else:
            ignore_dbs = list(self._sys_databases())
            query_sql = f"{query_sql} {list_to_in_str('WHERE TABLE_SCHEMA NOT IN', ignore_dbs)}"

        result = self._execute_pandas(query_sql)

        mv_list = []
        for i in range(len(result)):
            mv_list.append(
                {
                    "identifier": self.identifier(
                        catalog_name=current_catalog,
                        database_name=str(result["TABLE_SCHEMA"][i]),
                        table_name=str(result["TABLE_NAME"][i]),
                    ),
                    "catalog_name": current_catalog,
                    "database_name": result["TABLE_SCHEMA"][i],
                    "schema_name": "",
                    "table_name": result["TABLE_NAME"][i],
                    "definition": result["MATERIALIZED_VIEW_DEFINITION"][i],
                    "table_type": "mv",
                }
            )

        return mv_list

    # ==================== Database Management ====================

    def _sys_databases(self) -> Set[str]:
        """System databases to filter out (StarRocks-specific)."""
        # Include MySQL system databases plus StarRocks-specific ones
        mysql_sys = super()._sys_databases()
        starrocks_sys = {"_statistics_"}
        return mysql_sys | starrocks_sys

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

        StarRocks format: `catalog`.`database`.`table`
        """
        catalog_name = self._resolve_catalog(catalog_name)

        if catalog_name:
            if database_name:
                return f"`{catalog_name}`.`{database_name}`.`{table_name}`"
            else:
                return f"`{table_name}`"
        else:
            if database_name:
                return f"`{database_name}`.`{table_name}`"
            return f"`{table_name}`"

    @override
    def _sqlalchemy_schema(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> str:
        """Get schema name for SQLAlchemy Inspector with catalog support."""
        database_name = database_name or self.database_name

        catalog_name = catalog_name or self.catalog_name or self.default_catalog()
        if database_name:
            return f"{catalog_name}.{database_name}"
        return None

    # ==================== Connection Cleanup ====================

    @override
    def close(self):
        """
        Close engine with special handling for PyMySQL cleanup errors.

        StarRocks may trigger PyMySQL struct.pack errors during cleanup,
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
            "db_type": "starrocks",
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "catalog": self.catalog_name,
            "database": self.database_name,
        }

    def get_type(self) -> str:
        """Return the database type."""
        return "starrocks"

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
        return {
            "supported": True,
            "dialect_family": "mysql-like",
            "requires": [
                "One of DUPLICATE KEY / PRIMARY KEY / UNIQUE KEY / AGGREGATE KEY",
                "DISTRIBUTED BY HASH(cols) BUCKETS N",
            ],
            "forbids": ["AUTO_INCREMENT", "FOREIGN KEY", "FULLTEXT INDEX", "CHECK"],
            "type_hints": {
                "unbounded VARCHAR": "VARCHAR(65533)",
                "TEXT": "STRING",
                "TIMESTAMP": "DATETIME",
                "TIMESTAMPTZ": "DATETIME",
                "TIME": "VARCHAR(20) (StarRocks has no native TIME)",
                "UUID": "VARCHAR(36)",
                "HUGEINT": "LARGEINT",
            },
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
        if not columns:
            return {"duplicate_key": [], "distributed_by": [], "buckets": 10}

        keys = self._score_keys(columns, max_keys=3)
        return {"duplicate_key": keys, "distributed_by": keys, "buckets": 10}

    @staticmethod
    def _score_keys(columns: List[Dict[str, Any]], max_keys: int = 3) -> List[str]:
        """Select key columns using priority rules.

        Priority:
          1. Columns with 'id' or '_id' suffix (+100)
          2. INT/BIGINT type columns (+50)
          3. Non-nullable columns preferred (+10)
          4. Fallback to first column
        """
        import re as _re

        scored = []
        for col in columns:
            name = col["name"]
            col_type = str(col.get("type", "")).upper()
            base_type = _re.sub(r"\(.*\)", "", col_type).strip()
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
            return [columns[0]["name"]]

        return [name for score, name in scored[:max_keys] if score > 0] or [columns[0]["name"]]

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()

        has_duplicate_key = "DUPLICATE KEY" in upper and "ON DUPLICATE KEY" not in upper
        if not (has_duplicate_key or any(k in upper for k in ("PRIMARY KEY", "UNIQUE KEY", "AGGREGATE KEY"))):
            errors.append("StarRocks DDL must define one of: DUPLICATE KEY / PRIMARY KEY / UNIQUE KEY / AGGREGATE KEY")

        if "DISTRIBUTED BY" not in upper:
            errors.append("StarRocks DDL must include a DISTRIBUTED BY clause")

        if "AUTO_INCREMENT" in upper:
            errors.append("StarRocks does not support AUTO_INCREMENT")

        if "FOREIGN KEY" in upper:
            errors.append("StarRocks does not support FOREIGN KEY")

        if "FULLTEXT" in upper:
            errors.append("StarRocks does not support FULLTEXT indexes")

        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> str | None:
        base = source_type.strip().upper()
        # Strip params for matching
        import re as _re

        base_noparam = _re.sub(r"\(.*\)", "", base).strip()
        # Deterministic overrides for well-known pairings
        overrides = {
            "HUGEINT": "LARGEINT",
            "TIMESTAMP": "DATETIME",
            "TIMESTAMPTZ": "DATETIME",
            "TIMESTAMP WITH TIME ZONE": "DATETIME",
            "TEXT": "STRING",
            "TIME": "VARCHAR(20)",
            "UUID": "VARCHAR(36)",
        }
        return overrides.get(base_noparam)
