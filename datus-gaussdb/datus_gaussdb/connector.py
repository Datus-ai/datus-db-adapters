# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from dataclasses import dataclass
from typing import Any, Dict, List, Set, Union, override
from urllib.parse import quote_plus

from datus_db_core import get_logger
from datus_postgresql import PostgreSQLConnector

from . import sa_dialect  # noqa: F401  (registers the gaussdb SQLAlchemy dialect)
from .config import GaussDBConfig

logger = get_logger(__name__)


@dataclass
class DbTraits:
    """Per-database feature probe results, cached on first access.

    GaussDB variants (centralized vs distributed, openGauss vs GaussDB
    Kernel 50x, A/B/PG compatibility modes) are detected by probing catalog
    features, never by parsing version strings.
    """

    compat_mode: str = "A"
    is_distributed: bool = False
    has_matviews: bool = True


class GaussDBConnector(PostgreSQLConnector):
    """GaussDB / openGauss database connector.

    GaussDB is PostgreSQL-compatible on the wire; this connector inherits
    all execution and metadata logic from PostgreSQLConnector and connects
    through the official ``gaussdb`` driver (sha256/md5/sm3 authentication)
    via the ``gaussdb+psycopg`` SQLAlchemy dialect. On macOS, where the
    official libpq is unavailable, it uses the isolated
    ``gaussdb+psycopg2`` compatibility dialect by default.
    """

    def __init__(self, config: Union[GaussDBConfig, dict]):
        if isinstance(config, dict):
            config = GaussDBConfig(**config)
        elif not isinstance(config, GaussDBConfig):
            raise TypeError(f"config must be GaussDBConfig or dict, got {type(config)}")

        super().__init__(config)
        # PostgreSQLConnector fixes dialect="postgresql" and a psycopg2
        # connection string; both must be re-pointed at a GaussDB dialect.
        # The engine is created lazily, so nothing has connected yet.
        self.dialect = "gaussdb"
        self.config = config
        self.connection_string = self._build_connection_string(self._default_database)
        self._traits_cache: Dict[str, DbTraits] = {}

    # ==================== Connection ====================

    @override
    def _build_connection_string(self, database_name: str) -> str:
        prefix = "gaussdb+psycopg2" if self.config.driver == "psycopg2" else "gaussdb+psycopg"
        encoded_username = quote_plus(self.username) if self.username else ""
        encoded_password = quote_plus(self.password) if self.password else ""
        return (
            f"{prefix}://{encoded_username}:{encoded_password}"
            f"@{self.host}:{self.port}/{database_name}?sslmode={self.config.sslmode}"
        )

    # ==================== System Resources ====================

    @override
    def _sys_schemas(self) -> Set[str]:
        return super()._sys_schemas() | {
            "blockchain",
            "cstore",
            "db4ai",
            "dbe_perf",
            "dbe_pldebugger",
            "dbe_pldeveloper",
            "dbe_sql_util",
            "pkg_service",
            "snapshot",
            "sqladvisor",
        }

    # ==================== Feature Probing ====================

    def _get_traits(self, database_name: str = "") -> DbTraits:
        key = database_name or self._default_database
        traits = self._traits_cache.get(key)
        if traits is not None:
            return traits

        # NOTE: probe SQL must stay compatibility-mode neutral — no `= ''`
        # null-checks (A mode) and no `||` concat (B mode). Each probe runs
        # on its own connection: a failed probe aborts the transaction and
        # would poison the probes after it.
        traits = DbTraits()
        try:
            row = self._probe_scalar(
                "SELECT datcompatibility FROM pg_database WHERE datname = current_database()", database_name
            )
            if row:
                traits.compat_mode = str(row).strip().upper()
        except Exception as e:
            logger.warning(f"GaussDB compatibility-mode probe failed for database '{key}', assuming 'A': {e}")

        # The pgxc_* catalogs exist (empty) even on centralized
        # openGauss/GaussDB — deployment form is told by the number of
        # registered cluster nodes, not by catalog presence.
        try:
            nodes = self._probe_scalar("SELECT count(*) FROM pgxc_node", database_name)
            traits.is_distributed = int(nodes or 0) > 1
        except Exception:
            traits.is_distributed = False

        # openGauss builds may lack the pg_matviews view entirely.
        try:
            self._probe_scalar("SELECT count(*) FROM pg_matviews WHERE 1 = 0", database_name)
            traits.has_matviews = True
        except Exception:
            traits.has_matviews = False

        self._traits_cache[key] = traits
        return traits

    def _probe_scalar(self, sql: str, database_name: str = ""):
        with self._conn(database_name=database_name) as conn:
            return self._exec_scalar(conn, sql)

    @staticmethod
    def _exec_scalar(conn, sql: str):
        from sqlalchemy import text

        return conn.execute(text(sql)).scalar()

    # ==================== Metadata ====================

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """Get list of schemas in the current database.

        openGauss/GaussDB restrict ``information_schema.schemata`` to schemas
        the current user owns, which breaks discovery for ordinary login
        roles; ``pg_namespace`` lists every schema regardless of ownership.
        """
        result = self._execute_pandas(
            "SELECT nspname AS schema_name FROM pg_namespace ORDER BY nspname",
            database_name=database_name,
        )
        schemas = result["schema_name"].tolist()

        if not include_sys:
            sys_schemas = self._sys_schemas()
            schemas = [
                s
                for s in schemas
                if s not in sys_schemas and not s.startswith("pg_temp_") and not s.startswith("pg_toast_temp_")
            ]
        return schemas

    @override
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        if not self._get_traits(database_name).has_matviews:
            return []
        return super().get_materialized_views(catalog_name, database_name, schema_name)

    @override
    def _get_ddl(
        self,
        schema_name: str,
        table_name: str,
        object_type: str = "TABLE",
        database_name: str = "",
    ) -> str:
        ddl = super()._get_ddl(schema_name, table_name, object_type, database_name)
        if object_type.upper() != "TABLE" or not ddl:
            return ddl
        if not self._get_traits(database_name).is_distributed:
            return ddl
        distribution = self._get_distribution_clause(schema_name, table_name, database_name)
        if distribution and "DISTRIBUTE BY" not in ddl.upper():
            ddl = f"{ddl.rstrip().rstrip(';')}\n{distribution};"
        return ddl

    def _get_distribution_clause(self, schema_name: str, table_name: str, database_name: str = "") -> str:
        """Reconstruct the DISTRIBUTE BY clause from pgxc_class (distributed only)."""
        sql = """
            SELECT x.pclocatortype, x.pcattnum
            FROM pgxc_class x
            JOIN pg_class c ON c.oid = x.pcrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :table_name AND n.nspname = :schema_name
        """
        try:
            from sqlalchemy import text

            with self._conn(database_name=database_name) as conn:
                row = conn.execute(text(sql), {"table_name": table_name, "schema_name": schema_name}).fetchone()
            if not row:
                return ""
            locator_type, attnums = row[0], row[1]
            if locator_type == "R":
                return "DISTRIBUTE BY REPLICATION"
            if locator_type == "N":
                return "DISTRIBUTE BY ROUNDROBIN"
            if locator_type == "H" and attnums:
                columns = self._get_attribute_names(schema_name, table_name, attnums, database_name)
                if columns:
                    return f"DISTRIBUTE BY HASH ({', '.join(columns)})"
            return ""
        except Exception as e:
            logger.warning(f"Failed to read distribution policy for {schema_name}.{table_name}: {e}")
            return ""

    def _get_attribute_names(self, schema_name: str, table_name: str, attnums, database_name: str = "") -> List[str]:
        if isinstance(attnums, str):
            nums = [int(x) for x in attnums.split()]
        elif isinstance(attnums, (list, tuple)):
            nums = [int(x) for x in attnums]
        else:
            nums = [int(attnums)]
        from sqlalchemy import text

        sql = """
            SELECT a.attnum, a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = :table_name AND n.nspname = :schema_name AND a.attnum > 0
        """
        with self._conn(database_name=database_name) as conn:
            rows = conn.execute(text(sql), {"table_name": table_name, "schema_name": schema_name}).fetchall()
        by_num = {int(r[0]): r[1] for r in rows}
        # A partially resolved key (e.g. a dropped column) would yield a
        # DISTRIBUTE BY clause with fewer columns than the real one, which is
        # worse than emitting no clause at all.
        missing = [n for n in nums if n not in by_num]
        if missing:
            logger.warning(
                f"Unresolved distribution key attnums {missing} for {schema_name}.{table_name}; "
                "omitting the DISTRIBUTE BY clause"
            )
            return []
        return [by_num[n] for n in nums]

    # ==================== MigrationTargetMixin ====================

    @override
    def describe_migration_capabilities(self) -> Dict[str, Any]:
        traits = self._get_traits()
        capabilities = super().describe_migration_capabilities()
        capabilities["dialect_family"] = "gaussdb"
        notes = []
        if traits.compat_mode == "A":
            notes.append(
                "This database runs in 'A' (Oracle) compatibility mode: empty "
                "strings are stored as NULL — source data containing empty "
                "strings will not round-trip."
            )
        if traits.is_distributed:
            notes.append(
                "Distributed deployment: choose a DISTRIBUTE BY HASH key for large "
                "tables; distribution key columns cannot be UPDATEd."
            )
        capabilities["notes"] = notes
        return capabilities
