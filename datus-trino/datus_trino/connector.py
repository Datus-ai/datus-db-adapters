# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Optional, Set, Union, override
from urllib.parse import quote_plus

from sqlalchemy import create_engine

from datus_db_core import CatalogSupportMixin, MigrationTargetMixin, get_logger
from datus_sqlalchemy import SQLAlchemyConnector

from .config import TrinoConfig

logger = get_logger(__name__)

TRINO_DIALECT = "trino"


class TrinoConnector(SQLAlchemyConnector, CatalogSupportMixin, MigrationTargetMixin):
    """
    Trino database connector.

    Trino supports multi-catalog with a three-level hierarchy: catalog -> schema -> table.
    This connector implements CatalogSupportMixin for catalog management.
    """

    def __init__(self, config: Union[TrinoConfig, dict]):
        """
        Initialize Trino connector.

        Args:
            config: TrinoConfig object or dict with configuration
        """
        if isinstance(config, dict):
            config = TrinoConfig(**config)
        elif not isinstance(config, TrinoConfig):
            raise TypeError(f"config must be TrinoConfig or dict, got {type(config)}")

        self.trino_config = config
        self.host = config.host
        self.port = config.port
        self.user = config.username

        # Build connection string: trino://user:pass@host:port/catalog/schema
        encoded_user = quote_plus(config.username)
        encoded_password = quote_plus(config.password) if config.password else ""
        auth_part = f"{encoded_user}:{encoded_password}@" if config.password else f"{encoded_user}@"

        connection_string = (
            f"trino://{auth_part}{config.host}:{config.port}"
            f"/{config.catalog}/{config.schema_name}"
            f"?http_scheme={config.http_scheme}"
        )

        super().__init__(
            connection_string,
            dialect=TRINO_DIALECT,
            timeout_seconds=config.timeout_seconds,
        )

        self._verify_ssl = config.verify
        self._http_scheme = config.http_scheme
        self.dialect = TRINO_DIALECT
        self._default_catalog = config.catalog
        self._default_schema = config.schema_name
        self._default_database = config.schema_name  # In Trino, schemas map to databases

    # ==================== Connection Management ====================

    @override
    def _ensure_engine(self):
        """Create engine with SSL verification setting. Thread-safe."""
        if self.engine and self._owns_engine:
            return self.engine
        with self._engine_lock:
            if self.engine and self._owns_engine:
                return self.engine
            self.engine = create_engine(
                self.connection_string,
                pool_size=10,
                max_overflow=20,
                pool_timeout=self.timeout_seconds,
                pool_recycle=3600,
                pool_pre_ping=True,
                connect_args={"verify": self._verify_ssl, "http_scheme": self._http_scheme},
            )
            self._owns_engine = True
            return self.engine

    # ==================== Context Manager Support ====================

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        return False

    # ==================== Catalog Management (CatalogSupportMixin) ====================

    @override
    def default_catalog(self) -> str:
        """Trino default catalog."""
        return self.trino_config.catalog

    @override
    def get_catalogs(self) -> List[str]:
        """Get list of catalogs."""
        result = self._execute_pandas("SHOW CATALOGS")
        if result.empty:
            return []
        return result.iloc[:, 0].tolist()

    @override
    def switch_catalog(self, catalog_name: str) -> None:
        """Switch to a different catalog.

        Args:
            catalog_name: Name of the catalog to switch to
        """
        self.catalog_name = catalog_name

    # ==================== Metadata Retrieval ====================

    @override
    def _sys_databases(self) -> Set[str]:
        """System databases/schemas to filter out."""
        return {"information_schema"}

    @override
    def _sys_schemas(self) -> Set[str]:
        """System schemas to filter out."""
        return {"information_schema"}

    @override
    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of databases (schemas in Trino)."""
        return self.get_schemas(catalog_name=catalog_name, include_sys=include_sys)

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of schemas from catalog."""
        catalog = catalog_name or self.catalog_name
        result = self._execute_pandas(f'SHOW SCHEMAS FROM "{catalog}"')
        if result.empty:
            return []
        schemas = result.iloc[:, 0].tolist()
        if not include_sys:
            sys_schemas = self._sys_schemas()
            schemas = [s for s in schemas if s.lower() not in sys_schemas]
        return schemas

    @override
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
        """Get list of table names."""
        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name
        result = self._execute_pandas(f'SHOW TABLES FROM "{catalog}"."{schema}"')
        if result.empty:
            return []
        return [
            self._qualify(name, catalog, schema, catalog_name, schema_name or database_name)
            for name in result.iloc[:, 0].tolist()
        ]

    @override
    def get_views(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        """Get list of view names."""
        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name
        try:
            safe_schema = schema.replace("'", "''")
            result = self._execute_pandas(
                f"SELECT table_name FROM \"{catalog}\".information_schema.views WHERE table_schema = '{safe_schema}'"
            )
            if result.empty:
                return []
            return [
                self._qualify(name, catalog, schema, catalog_name, schema_name or database_name)
                for name in result.iloc[:, 0].tolist()
            ]
        except Exception as e:
            logger.warning(f"Failed to get views: {e}")
            return []

    def _show_create_definition(self, object_kind: str, catalog: str, schema: str, table: str) -> str:
        """Run `SHOW CREATE TABLE/VIEW` and return the first cell (the DDL text)."""
        sql = f'SHOW CREATE {object_kind} "{catalog}"."{schema}"."{table}"'
        result = self._execute_pandas(sql)
        if result.empty:
            return ""
        return str(result.iloc[0, 0])

    def _objects_with_ddl(
        self,
        object_kind: str,
        names: List[str],
        catalog: str,
        schema: str,
        tables_filter: Optional[List[str]],
    ) -> List[Dict[str, str]]:
        """Assemble `[{identifier, catalog_name, database_name, schema_name, table_name,
        table_type, definition}, ...]` for the requested object kind."""
        result: List[Dict[str, str]] = []
        for name in names:
            full = self.full_name(catalog_name=catalog, schema_name=schema, table_name=name)
            if tables_filter and full not in tables_filter:
                continue
            try:
                ddl = self._show_create_definition(object_kind, catalog, schema, name)
            except Exception as e:
                logger.warning(f"Could not get DDL for {full}: {e}")
                ddl = f"-- DDL not available for {name}"
            result.append(
                {
                    "identifier": full,
                    "catalog_name": catalog,
                    "database_name": schema,
                    "schema_name": schema,
                    "table_name": name,
                    "table_type": object_kind.lower(),
                    "definition": ddl,
                }
            )
        return result

    @override
    def get_tables_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        tables: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Get tables with DDL statements (via `SHOW CREATE TABLE`)."""
        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name
        filter_tables = self._reset_filter_tables(tables, catalog_name, database_name, schema_name)
        names = self.get_tables(catalog_name=catalog, schema_name=schema)
        return self._objects_with_ddl("TABLE", names, catalog, schema, filter_tables)

    @override
    def get_views_with_ddl(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> List[Dict[str, str]]:
        """Get views with DDL statements (via `SHOW CREATE VIEW`)."""
        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name
        names = self.get_views(catalog_name=catalog, schema_name=schema)
        return self._objects_with_ddl("VIEW", names, catalog, schema, None)

    @override
    def get_schema(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Get table schema information."""
        if not table_name:
            return []

        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name

        safe_catalog = catalog.replace("'", "''")
        safe_schema = schema.replace("'", "''")
        safe_table = table_name.replace("'", "''")
        sql = (
            f"SELECT column_name, data_type, is_nullable, column_default, comment "
            f'FROM "{catalog}".information_schema.columns '
            f"WHERE table_catalog = '{safe_catalog}' "
            f"AND table_schema = '{safe_schema}' "
            f"AND table_name = '{safe_table}' "
            f"ORDER BY ordinal_position"
        )
        query_result = self._execute_pandas(sql)

        result = []
        for i in range(len(query_result)):
            result.append(
                {
                    "cid": i,
                    "name": query_result["column_name"][i],
                    "type": query_result["data_type"][i],
                    "nullable": query_result["is_nullable"][i] == "YES",
                    "default_value": query_result["column_default"][i],
                    "pk": False,  # Trino doesn't have primary keys in INFORMATION_SCHEMA
                    "comment": query_result["comment"][i] if "comment" in query_result.columns else None,
                }
            )
        return result

    # ==================== Full Name Construction ====================

    # quote_identifier: uses BaseSqlConnector default (ANSI double quotes)

    @override
    def full_name(
        self,
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
        table_name: str = "",
    ) -> str:
        """
        Build fully-qualified table name.

        Trino format: "catalog"."schema"."table"
        """
        catalog = catalog_name or self.catalog_name
        schema = schema_name or database_name or self.schema_name

        if catalog and schema:
            return (
                f"{self.quote_identifier(catalog)}.{self.quote_identifier(schema)}.{self.quote_identifier(table_name)}"
            )
        elif schema:
            return f"{self.quote_identifier(schema)}.{self.quote_identifier(table_name)}"
        return self.quote_identifier(table_name)

    @override
    def _sqlalchemy_schema(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> Optional[str]:
        """Get schema name for SQLAlchemy Inspector."""
        schema = schema_name or database_name or self.schema_name
        return schema if schema else None

    @override
    def do_switch_context(self, conn, catalog_name: str = "", database_name: str = "", schema_name: str = ""):
        """No-op: Trino uses fully-qualified names. Context is tracked in thread-local state."""
        pass

    # ==================== Utility Methods ====================

    def to_dict(self) -> Dict[str, Any]:
        """Convert connector to serializable dictionary."""
        return {
            "db_type": TRINO_DIALECT,
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "catalog": self.catalog_name,
            "schema": self.schema_name,
        }

    def get_type(self) -> str:
        """Return the database type."""
        return TRINO_DIALECT

    @override
    def test_connection(self) -> bool:
        """Test the database connection."""
        try:
            return super().test_connection()
        finally:
            try:
                self.close()
            except Exception as e:
                logger.debug(f"Ignoring cleanup error during test: {e}")

    # ==================== MigrationTargetMixin ====================

    def _detect_catalog_type(self) -> str:
        """Detect the current catalog's underlying connector type.

        Returns one of: ``"hive"``, ``"iceberg"``, ``"delta"``, ``"jdbc"``,
        ``"unknown"``. Queries ``system.metadata.catalogs`` when possible.
        Callers may override (or tests monkeypatch) to provide a known type.
        """
        catalog = getattr(self, "catalog_name", "") or ""
        if not catalog:
            return "unknown"
        try:
            # system.metadata.catalogs lists catalog_name and connector_name
            result = self.execute_query(
                f"SELECT connector_name FROM system.metadata.catalogs WHERE catalog_name = '{catalog}'",
                result_format="list",
            )
            rows = getattr(result, "sql_return", None) or []
            if rows and isinstance(rows, list) and rows[0]:
                first = rows[0]
                if isinstance(first, dict):
                    raw = first.get("connector_name", "")
                elif isinstance(first, (list, tuple)):
                    raw = first[0] if first else ""
                else:
                    raw = first
                connector_name = str(raw).lower()
                if "iceberg" in connector_name:
                    return "iceberg"
                if "delta" in connector_name:
                    return "delta"
                if "hive" in connector_name:
                    return "hive"
                if any(k in connector_name for k in ("mysql", "postgres", "oracle", "sqlserver", "jdbc")):
                    return "jdbc"
        except Exception as e:
            logger.debug(f"_detect_catalog_type probe failed: {e}")
        return "unknown"

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        try:
            catalog_type = self._detect_catalog_type()
        except Exception as e:
            logger.debug(f"_detect_catalog_type raised; falling back to generic: {e}")
            catalog_type = "unknown"

        if catalog_type == "hive":
            return {
                "supported": True,
                "dialect_family": "trino-hive",
                "requires": [],
                "forbids": ["DUPLICATE KEY (StarRocks)", "ENGINE = ... (ClickHouse/MySQL)"],
                "type_hints": {
                    "format": "Add WITH (format = 'PARQUET') for efficient storage",
                    "partitioned_by": "Use WITH (partitioned_by = ARRAY['col']) for partitioning",
                    "bucketed_by": "Use WITH (bucketed_by = ARRAY['col'], bucket_count = N) for bucketing",
                },
                "example_ddl": (
                    "CREATE TABLE catalog.schema.t (\n"
                    "  id BIGINT,\n"
                    "  ds VARCHAR\n"
                    ") WITH (format = 'PARQUET', partitioned_by = ARRAY['ds'])"
                ),
            }
        if catalog_type == "iceberg":
            return {
                "supported": True,
                "dialect_family": "trino-iceberg",
                "requires": [],
                "forbids": ["DUPLICATE KEY (StarRocks)", "ENGINE = ... (ClickHouse/MySQL)"],
                "type_hints": {
                    "partitioning": "Use WITH (partitioning = ARRAY['month(ds)', 'bucket(id, 4)'])",
                    "format": "Default format is PARQUET; ORC also supported",
                    "location": "Optional WITH (location = 's3://...') for custom table location",
                },
                "example_ddl": (
                    "CREATE TABLE catalog.schema.t (\n"
                    "  id BIGINT,\n"
                    "  ds DATE\n"
                    ") WITH (partitioning = ARRAY['month(ds)'])"
                ),
            }
        if catalog_type == "delta":
            return {
                "supported": True,
                "dialect_family": "trino-delta",
                "requires": [],
                "forbids": ["DUPLICATE KEY (StarRocks)", "ENGINE = ... (ClickHouse/MySQL)"],
                "type_hints": {
                    "partitioned_by": "Use WITH (partitioned_by = ARRAY['col']) for partitioning",
                    "location": "Optional WITH (location = 's3://...') for custom table location",
                },
                "example_ddl": (
                    "CREATE TABLE catalog.schema.t (\n  id BIGINT,\n  ds DATE\n) WITH (partitioned_by = ARRAY['ds'])"
                ),
            }
        if catalog_type == "jdbc":
            return {
                "supported": True,
                "dialect_family": "trino-jdbc",
                "requires": [],
                "forbids": ["DUPLICATE KEY (StarRocks)", "ENGINE = ... (ClickHouse/MySQL)"],
                "type_hints": {
                    "note": "Trino JDBC catalogs pass DDL through to the underlying engine; "
                    "check that engine's own requirements",
                },
                "example_ddl": ("CREATE TABLE catalog.schema.t (\n  id BIGINT,\n  name VARCHAR\n)"),
            }
        # unknown / generic
        return {
            "supported": True,
            "dialect_family": "trino-generic",
            "requires": [],
            "forbids": ["DUPLICATE KEY (StarRocks)", "ENGINE = ... (ClickHouse/MySQL)"],
            "type_hints": {},
            "note": f"Catalog connector unknown ({getattr(self, 'catalog_name', '') or 'unset'}); "
            "DDL requirements depend on underlying engine",
            "example_ddl": "CREATE TABLE catalog.schema.t (\n  id BIGINT,\n  name VARCHAR\n)",
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not columns:
            return {}
        try:
            catalog_type = self._detect_catalog_type()
        except Exception:
            catalog_type = "unknown"

        if catalog_type == "hive":
            # Suggest partitioned_by only when a conventional partition column exists
            partition_cols = [c["name"] for c in columns if c["name"].lower() in ("ds", "dt", "partition_date")]
            layout: Dict[str, Any] = {"format": "PARQUET"}
            if partition_cols:
                layout["partitioned_by"] = partition_cols[:1]
            return layout
        if catalog_type == "iceberg":
            partition_cols = [c["name"] for c in columns if c["name"].lower() in ("ds", "dt", "partition_date")]
            if partition_cols:
                return {"partitioning": [f"month({partition_cols[0]})"]}
            return {}
        return {}

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()

        if "DUPLICATE KEY" in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; Trino catalogs do not support it")
        if "BUCKETS" in upper and "DISTRIBUTED BY" in upper:
            errors.append(
                "DISTRIBUTED BY ... BUCKETS is StarRocks syntax; Trino uses WITH (bucketed_by = ARRAY[...]) for Hive"
            )
        if "ENGINE =" in upper or "ENGINE=" in upper:
            errors.append("ENGINE clause is MySQL/ClickHouse syntax; Trino uses WITH (...) table properties")
        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "DECIMAL(38,0)",
            "LARGEINT": "DECIMAL(38,0)",
            "STRING": "VARCHAR",
            "TEXT": "VARCHAR",
        }
        return overrides.get(base)
