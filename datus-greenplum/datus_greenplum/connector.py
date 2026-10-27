# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Optional, Set, Union, override

from datus_db_core import MigrationTargetMixin, get_logger
from datus_postgresql import PostgreSQLConnector

from .config import GreenplumConfig

logger = get_logger(__name__)

_GREENPLUM_INTEGER_TYPES = frozenset({"INT", "INTEGER", "BIGINT", "SMALLINT", "INT2", "INT4", "INT8"})


def _escape_literal(value: str) -> str:
    """Escape a string for use in SQL string literals (single-quote context)."""
    return value.replace("'", "''")


class GreenplumConnector(PostgreSQLConnector, MigrationTargetMixin):
    """Greenplum database connector.

    Greenplum is based on PostgreSQL and uses the same wire protocol.
    This connector inherits from PostgreSQLConnector and overrides
    Greenplum-specific behaviors such as system databases/schemas
    and DDL generation with distribution policy.
    """

    def __init__(self, config: Union[GreenplumConfig, dict]):
        """Initialize Greenplum connector.

        Args:
            config: GreenplumConfig object or dict with configuration
        """
        if isinstance(config, dict):
            config = GreenplumConfig(**config)
        elif not isinstance(config, GreenplumConfig):
            raise TypeError(f"config must be GreenplumConfig or dict, got {type(config)}")

        self.greenplum_config = config

        # GreenplumConfig IS-A PostgreSQLConfig, so pass directly to parent
        super().__init__(config)
        # Keep reference to the original Greenplum config
        self.config = config
        self._distribution_policy_key_column: Optional[str] = None

    # ==================== System Resources ====================

    @override
    def _sys_databases(self) -> Set[str]:
        """System databases to filter out (includes Greenplum-specific databases)."""
        return super()._sys_databases() | {"gpperfmon"}

    @override
    def _sys_schemas(self) -> Set[str]:
        """System schemas to filter out (includes Greenplum-specific schemas)."""
        return super()._sys_schemas() | {
            "gp_toolkit",
            "pg_aoseg",
            "pg_bitmapindex",
        }

    @override
    def get_materialized_views(
        self, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> List[str]:
        """Greenplum 6.x does not expose a `pg_matviews` system view, and materialized
        views themselves are not part of the GP 6 core. Returning an empty list keeps
        callers (e.g. `DBFuncTool.list_tables`) from raising when probing for MVs."""
        return []

    # ==================== DDL Generation ====================

    def _get_distribution_policy_key_column(self) -> str:
        """Return the catalog column that stores distribution key attnums."""
        if self._distribution_policy_key_column is not None:
            return self._distribution_policy_key_column

        sql = """
            SELECT attname
            FROM pg_attribute
            WHERE attrelid = 'pg_catalog.gp_distribution_policy'::regclass
              AND attname IN ('distkey', 'attrnums')
              AND NOT attisdropped
            ORDER BY CASE attname WHEN 'distkey' THEN 0 ELSE 1 END
            LIMIT 1
        """
        result = self._execute_pandas(sql)
        if result.empty:
            raise RuntimeError("Could not find distribution key column in gp_distribution_policy")

        policy_key_column = str(result["attname"].iloc[0])
        if policy_key_column not in {"distkey", "attrnums"}:
            raise RuntimeError(f"Unexpected distribution key column: {policy_key_column}")

        self._distribution_policy_key_column = policy_key_column
        return policy_key_column

    def _get_distribution_policy(self, schema_name: str, table_name: str) -> Optional[str]:
        """Get distribution policy clause for a Greenplum table.

        Args:
            schema_name: Schema name
            table_name: Table name

        Returns:
            Distribution policy clause string, or None on error
        """
        safe_schema = _escape_literal(schema_name)
        safe_table = _escape_literal(table_name)

        try:
            # GP 5 and older use `attrnums`; GP 6+ renamed it to `distkey`.
            policy_key_column = self._get_distribution_policy_key_column()
            sql = f"""
                SELECT a.attname
                FROM gp_distribution_policy dp
                JOIN pg_class c ON dp.localoid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                LEFT JOIN LATERAL unnest(dp.{policy_key_column}) WITH ORDINALITY AS dist(attnum, ord)
                    ON TRUE
                LEFT JOIN pg_attribute a ON a.attrelid = c.oid
                    AND a.attnum = dist.attnum
                WHERE n.nspname = '{safe_schema}'
                  AND c.relname = '{safe_table}'
                ORDER BY dist.ord
            """
            result = self._execute_pandas(sql)

            if result.empty or (len(result) == 1 and result["attname"][0] is None):
                return "DISTRIBUTED RANDOMLY"

            dist_cols = [self.quote_identifier(row) for row in result["attname"].tolist() if row is not None]
            if dist_cols:
                return f"DISTRIBUTED BY ({', '.join(dist_cols)})"
            return "DISTRIBUTED RANDOMLY"
        except Exception as e:
            logger.warning(f"Could not get distribution policy for {schema_name}.{table_name}: {e}")
            return None

    @override
    def _get_ddl(self, schema_name: str, table_name: str, object_type: str = "TABLE") -> str:
        """Get DDL for a table/view, including Greenplum distribution policy for tables.

        Args:
            schema_name: Schema name
            table_name: Table name
            object_type: Object type (TABLE, VIEW, MATERIALIZED VIEW)

        Returns:
            DDL statement as string
        """
        ddl = super()._get_ddl(schema_name, table_name, object_type)

        # Append distribution policy for tables
        if object_type == "TABLE" and ddl.startswith("CREATE TABLE"):
            dist_policy = self._get_distribution_policy(schema_name, table_name)
            if dist_policy is not None:
                create_part, alter_lines = self._partition_table_ddl(ddl)
                # Insert distribution policy before the trailing semicolon of CREATE TABLE
                if create_part.endswith(";"):
                    create_part = create_part[:-1] + f"\n{dist_policy};"
                else:
                    create_part += f"\n{dist_policy}"
                ddl = "\n".join([create_part] + alter_lines) if alter_lines else create_part

        return ddl

    # ==================== Storage Info ====================

    def get_storage_info(self, schema_name: str = "", table_name: str = "") -> Optional[Dict[str, Any]]:
        """Get Greenplum-specific storage info for a table.

        Args:
            schema_name: Schema name
            table_name: Table name

        Returns:
            Dictionary with storage info or None
        """
        schema_name = schema_name or self.schema_name
        if not table_name:
            return None

        safe_schema = _escape_literal(schema_name)
        safe_table = _escape_literal(table_name)

        try:
            sql = f"""
                SELECT
                    c.relstorage,
                    CASE c.relstorage
                        WHEN 'h' THEN 'heap'
                        WHEN 'a' THEN 'append-optimized'
                        WHEN 'c' THEN 'column-oriented'
                        WHEN 'x' THEN 'external'
                        ELSE 'unknown'
                    END AS storage_type
                FROM pg_class c
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = '{safe_schema}'
                  AND c.relname = '{safe_table}'
            """
            result = self._execute_pandas(sql)
            if not result.empty:
                return {
                    "storage_code": result["relstorage"][0],
                    "storage_type": result["storage_type"][0],
                }
        except Exception as e:
            logger.warning(f"Could not get storage info for {schema_name}.{table_name}: {e}")

        return None

    # ==================== MigrationTargetMixin ====================

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "postgres-like",
            "requires": [],  # DISTRIBUTED BY is recommended, not strictly required
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH with BUCKETS (StarRocks-only; use DISTRIBUTED BY without BUCKETS)",
            ],
            "type_hints": {
                "HUGEINT": "NUMERIC(38,0) (Greenplum has no HUGEINT)",
                "unbounded VARCHAR": "TEXT (prefer over unbounded VARCHAR)",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
                "distribution": "Add DISTRIBUTED BY (<col>) or DISTRIBUTED RANDOMLY for even data layout",
            },
            "example_ddl": (
                "CREATE TABLE public.t (\n  id BIGINT NOT NULL,\n  name VARCHAR(255)\n)\nDISTRIBUTED BY (id)"
            ),
        }

    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not columns:
            return {"distributed_by": []}

        keys = self._gp_score_keys(columns, max_keys=1)
        return {"distributed_by": keys}

    @staticmethod
    def _gp_score_keys(columns: List[Dict[str, Any]], max_keys: int = 1) -> List[str]:
        """Select distribution columns for Greenplum.

        Priority:
          1. Columns with 'id' or '_id' suffix (+100)
          2. INT/BIGINT type columns (+50)
          3. Non-nullable columns (+10)
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
            if base_type in _GREENPLUM_INTEGER_TYPES:
                score += 50
            if not nullable:
                score += 10

            scored.append((score, name))

        scored.sort(key=lambda x: (-x[0], x[1]))

        if scored[0][0] == 0:
            return [columns[0]["name"]]

        return [name for score, name in scored[:max_keys]]

    def validate_ddl(self, ddl: str) -> List[str]:
        errors: List[str] = []
        upper = ddl.upper()

        if "DUPLICATE KEY" in upper:
            errors.append("DUPLICATE KEY is StarRocks-only syntax; Greenplum uses DISTRIBUTED BY or PRIMARY KEY")
        if "BUCKETS" in upper and "DISTRIBUTED BY HASH" in upper:
            errors.append(
                "DISTRIBUTED BY HASH(...) BUCKETS N is StarRocks syntax; Greenplum uses DISTRIBUTED BY (<cols>)"
            )
        if "ENGINE =" in upper or "ENGINE=" in upper:
            errors.append("ENGINE clause is MySQL/ClickHouse syntax; not supported in Greenplum")

        return errors

    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        import re as _re

        base = _re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "NUMERIC(38,0)",
            "DATETIME": "TIMESTAMP",
            "LARGEINT": "NUMERIC(38,0)",
        }
        return overrides.get(base)
