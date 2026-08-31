# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple, Union, override
from urllib.parse import quote_plus

from datus_db_core import get_logger
from datus_postgresql import PostgreSQLConnector

from ._ca_cert import as_path
from .config import DWSConfig

logger = get_logger(__name__)

# Verified against a live DWS 9.1.0 cluster by listing pg_namespace in full.
# GaussDB's list does not cover these: DWS ships Oracle-compatibility packages
# (dbms_*, utl_*), the column-store catalog, the recycle bin and the logical
# cluster registry, none of which belong to the user.
_DWS_SYS_SCHEMAS = frozenset(
    {
        "cstore",
        "dbms_job",
        "dbms_lob",
        "dbms_om",
        "dbms_output",
        "dbms_random",
        "dbms_sql",
        "gs_logical_cluster",
        "pg_recyclebin",
        "scheduler",
        "sys",
        "utl_file",
        "utl_raw",
    }
)

_SYS_SCHEMA_PREFIXES = ("dbms_", "utl_", "dbe_", "pkg_", "prvt_")

# ``TO GROUP`` names a node group and ``TABLESPACE`` an OBS tablespace; both are
# properties of the cluster that produced the DDL, so neither survives being
# replayed elsewhere.
#
# The name is either a quoted identifier — which may contain spaces, and escapes
# an inner quote by doubling it — or a bare one. Matching the quoted form first
# matters: an unquoted-only pattern truncates ``TABLESPACE "obs tbs"`` after
# ``"obs`` and leaves ``tbs"`` behind, producing DDL that no longer parses.
# ``[^\s;]+`` rather than ``\S+`` for the bare form so the statement's
# terminating semicolon is left in place.
_CLUSTER_SPECIFIC_CLAUSE_RE = re.compile(
    r'\n?[ \t]*(?:TO\s+GROUP|TABLESPACE)\s+(?:"(?:[^"]|"")*"|[^\s;]+)',
    re.IGNORECASE,
)

_INCOMPLETE_DDL_BANNER = (
    "-- WARNING: pg_get_tabledef() was unavailable for this table, so the DDL below was\n"
    "-- rebuilt from column metadata. Storage orientation, compression, distribution and\n"
    "-- partitioning are NOT represented."
)


@dataclass
class DWSTraits:
    """Per-database feature probe results, cached on first access.

    Probed from the catalog rather than parsed out of version strings, so a
    cluster that differs from the verified one degrades instead of misreporting.
    """

    compat_mode: str = ""
    has_matviews: bool = True
    enable_matview: bool = True


class DWSConnector(PostgreSQLConnector):
    """Datus connector for Huawei Cloud GaussDB(DWS).

    DWS speaks the PostgreSQL wire protocol and answers standard MD5
    authentication to protocol-3.0 clients, so this connector inherits the
    psycopg2 transport from PostgreSQLConnector unchanged. What it does not
    inherit is DWS's catalog surface: the system schema set, the native table
    definition function, and the Oracle-compatibility semantics of ORA mode.
    """

    def __init__(self, config: Union[DWSConfig, dict]):
        if isinstance(config, dict):
            config = DWSConfig(**config)
        elif not isinstance(config, DWSConfig):
            raise TypeError(f"config must be DWSConfig or dict, got {type(config)}")

        super().__init__(config)
        # PostgreSQLConnector fixes dialect="postgresql"; Datus routes
        # capabilities, parsing and prompts through the adapter's own dialect.
        self.dialect = "dws"
        self.config = config
        self.connection_string = self._build_connection_string(self._default_database)
        self._traits_cache: Dict[str, DWSTraits] = {}

    # ==================== Connection ====================

    @override
    def _build_connection_string(self, database_name: str) -> str:
        """Build the psycopg2 URL, carrying sslrootcert which the base class omits."""
        encoded_username = quote_plus(self.username) if self.username else ""
        encoded_password = quote_plus(self.password) if self.password else ""
        host = self.host
        # An IPv6 literal must be bracketed or the authority's port is unparseable.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"

        query = f"sslmode={self.config.sslmode}"
        # Inline PEM is spilled to a private file: psycopg2 takes a path, not bytes.
        sslrootcert = as_path(self.config.sslrootcert)
        if sslrootcert:
            query += f"&sslrootcert={quote_plus(sslrootcert)}"
        return f"postgresql+psycopg2://{encoded_username}:{encoded_password}@{host}:{self.port}/{database_name}?{query}"

    # ==================== System Resources ====================

    @override
    def _sys_schemas(self) -> Set[str]:
        return super()._sys_schemas() | set(_DWS_SYS_SCHEMAS)

    def _is_sys_schema(self, schema: str) -> bool:
        """Whether *schema* belongs to DWS rather than to the user.

        Deliberately does not treat the login role's own schema as system: that
        is exactly where an ordinary user's tables land.
        """
        return (
            schema in self._sys_schemas()
            or schema.startswith(_SYS_SCHEMA_PREFIXES)
            or schema.startswith("pg_temp_")
            or schema.startswith("pg_toast_temp_")
        )

    @override
    def get_schemas(self, catalog_name: str = "", database_name: str = "", include_sys: bool = False) -> List[str]:
        """List schemas from pg_namespace.

        ``information_schema.schemata`` only exposes schemas the current user
        owns, which hides objects an ordinary login role can still read.
        """
        result = self._execute_pandas(
            "SELECT nspname AS schema_name FROM pg_namespace ORDER BY nspname",
            database_name=database_name,
        )
        schemas = result["schema_name"].tolist()
        if not include_sys:
            schemas = [s for s in schemas if not self._is_sys_schema(s)]
        return schemas

    # ==================== Feature Probing ====================

    def _get_traits(self, database_name: str = "") -> DWSTraits:
        key = database_name or self._default_database
        traits = self._traits_cache.get(key)
        if traits is not None:
            return traits

        # Each probe runs on its own connection: a failed probe aborts the
        # transaction and would poison every probe after it. Probe SQL stays
        # compatibility-mode neutral — no `= ''` comparisons (ORA mode folds
        # empty strings to NULL) and no `||` concatenation.
        traits = DWSTraits()
        try:
            row = self._probe_scalar(
                "SELECT datcompatibility FROM pg_database WHERE datname = current_database()",
                database_name,
            )
            if row:
                traits.compat_mode = str(row).strip().upper()
        except Exception as e:
            logger.warning(f"DWS compatibility-mode probe failed for database '{key}': {e}")

        try:
            self._probe_scalar("SELECT count(*) FROM pg_matviews WHERE 1 = 0", database_name)
            traits.has_matviews = True
        except Exception:
            traits.has_matviews = False

        # Materialized views need 8.2.1.220+ *and* the GUC switched on; a fresh
        # cluster ships with it off. An unreadable GUC leaves the permissive
        # default so discovery is never blocked by a failed probe.
        try:
            value = self._probe_scalar("SHOW enable_matview", database_name)
            if value is not None:
                traits.enable_matview = str(value).strip().lower() in ("on", "true", "1")
        except Exception as e:
            logger.debug(f"DWS enable_matview probe failed for database '{key}', assuming enabled: {e}")

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
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        traits = self._get_traits(database_name)
        if not traits.has_matviews or not traits.enable_matview:
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
        """Return table DDL from DWS's own pg_get_tabledef().

        Rebuilding from column metadata the way the base class does drops
        orientation, compression, distribution, partitioning and even type
        precision, all of which pg_get_tabledef() reports faithfully.
        """
        database_name = database_name or self.database_name
        if object_type.upper() != "TABLE":
            return super()._get_ddl(schema_name, table_name, object_type, database_name)

        native = self._get_native_tabledef(schema_name, table_name, database_name)
        if native:
            return native

        fallback = super()._get_ddl(schema_name, table_name, object_type, database_name)
        logger.warning(
            f"pg_get_tabledef() unavailable for {schema_name}.{table_name}; "
            "falling back to reconstructed DDL without DWS storage attributes"
        )
        return f"{_INCOMPLETE_DDL_BANNER}\n{fallback}"

    def _get_native_tabledef(self, schema_name: str, table_name: str, database_name: str = "") -> str:
        """Call pg_get_tabledef(), returning "" when it is unusable.

        The object name is bound as a parameter rather than interpolated, and
        quoted so that mixed-case or reserved names resolve.
        """
        from sqlalchemy import text

        qualified = table_name if not schema_name else f"{schema_name}.{table_name}"
        try:
            with self._conn(database_name=database_name) as conn:
                rows = conn.execute(
                    text("SELECT * FROM pg_get_tabledef(:qualified)"),
                    {"qualified": qualified},
                ).fetchall()
        except Exception as e:
            logger.debug(f"pg_get_tabledef({qualified}) failed: {e}")
            return ""

        parts = [str(row[0]) for row in rows if row and row[0] is not None]
        return "\n".join(parts).strip()

    @staticmethod
    def strip_cluster_specific_clauses(ddl: str) -> str:
        """Remove clauses that cannot be replayed on a different cluster.

        ``TO GROUP`` names a node group and ``TABLESPACE`` an OBS tablespace of
        the source cluster. Keep them for semantic modelling, drop them before
        using the DDL against a migration target.
        """
        if not ddl:
            return ddl
        return _CLUSTER_SPECIFIC_CLAUSE_RE.sub("", ddl)

    # ==================== MigrationTargetMixin ====================

    @override
    def describe_migration_capabilities(self) -> Dict[str, Any]:
        capabilities = super().describe_migration_capabilities()
        capabilities["dialect_family"] = "dws"

        traits = self._get_traits()
        notes = [
            "Target is a shared-nothing MPP analytical warehouse; choose distribution "
            "and storage layout from workload evidence rather than defaults.",
            "DDL read from pg_get_tabledef() carries TO GROUP and TABLESPACE clauses "
            "that are specific to the source cluster; strip them before replaying.",
        ]
        if traits.compat_mode == "ORA":
            notes.append(
                "This database runs in ORA compatibility mode: empty strings are stored "
                "as NULL, so source data containing empty strings will not round-trip."
            )
            notes.append(
                "ORA mode also returns DATE as timestamp(0), DATE minus DATE as an "
                "interval, and 7/2 as 3.5 rather than integer division."
            )
        elif traits.compat_mode:
            notes.append(
                f"This database runs in {traits.compat_mode} compatibility mode, which "
                "Datus has not verified; type and empty-string semantics may differ."
            )
        capabilities["notes"] = notes
        return capabilities

    # ==================== Introspection helpers ====================

    def get_compatibility_mode(self, database_name: str = "") -> str:
        """Return the database's compatibility mode (ORA, TD or MySQL)."""
        return self._get_traits(database_name).compat_mode

    def get_server_version(self, database_name: str = "") -> Tuple[str, str]:
        """Return (server_version, full version banner).

        DWS reports a PostgreSQL 9.2.4 server_version alongside its own build
        string, so both are surfaced instead of parsing one out of the other.
        """
        version = self._probe_scalar("SHOW server_version", database_name)
        banner = self._probe_scalar("SELECT version()", database_name)
        return str(version or ""), str(banner or "")
