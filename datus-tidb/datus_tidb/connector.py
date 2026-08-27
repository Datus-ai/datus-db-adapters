# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from typing import Any, Dict, List, Optional, Set, Union, override

from datus_db_core import (
    TABLE_TYPE,
    DatusDbException,
    ErrorCode,
    MigrationTargetMixin,
    get_logger,
)
from datus_mysql import MySQLConfig, MySQLConnector

from .config import TiDBConfig

logger = get_logger(__name__)


class TiDBConnector(MySQLConnector, MigrationTargetMixin):
    """TiDB database connector.

    TiDB speaks the MySQL wire protocol, so connection handling, SQL execution
    and most metadata queries are inherited from :class:`MySQLConnector`. Only
    the places where TiDB genuinely differs are overridden:

    * ``METRICS_SCHEMA`` — a Prometheus-backed system database MySQL has no
      equivalent for, so the inherited filter would expose it as a user
      database.
    * Materialized views — TiDB has none, and ``information_schema`` has no
      ``MATERIALIZED_VIEWS`` table, so the inherited lookup fails with a bare
      ``1146`` instead of saying the feature is absent.
    * TiFlash — the columnar replica engine, exposed through
      ``information_schema.TIFLASH_REPLICA``; it has no MySQL counterpart.
    * DDL validation — TiDB parses ``CHECK`` and ``FULLTEXT`` without honouring
      them, which a MySQL-shaped validator has no reason to flag.
    """

    def __init__(self, config: Union[TiDBConfig, dict]):
        """
        Initialize TiDB connector.

        Args:
            config: TiDBConfig object or dict with configuration
        """
        if isinstance(config, dict):
            config = TiDBConfig(**config)
        elif not isinstance(config, TiDBConfig):
            raise TypeError(f"config must be TiDBConfig or dict, got {type(config)}")

        self.tidb_config = config

        super().__init__(
            MySQLConfig(
                host=config.host,
                port=config.port,
                username=config.username,
                password=config.password,
                database=config.database,
                charset=config.charset,
                autocommit=config.autocommit,
                timeout_seconds=config.timeout_seconds,
            )
        )
        # Set after super().__init__() so the inherited MySQL dialect does not
        # leak into SQL classification, skills, or the semantic layer.
        self.dialect = "tidb"

    # ==================== System Resources ====================

    @override
    def _sys_databases(self) -> Set[str]:
        """System databases to filter out.

        TiDB adds ``METRICS_SCHEMA`` (Prometheus-backed monitoring views) to the
        MySQL set. Callers lower-case before comparing, so one spelling is
        enough even though ``SHOW DATABASES`` reports it upper-cased.
        """
        return super()._sys_databases() | {"metrics_schema"}

    # ==================== Metadata Retrieval ====================

    @override
    def _get_metadata(
        self,
        table_type: TABLE_TYPE = "table",
        catalog_name: str = "",
        database_name: str = "",
    ) -> List[Dict[str, str]]:
        """Get table/view metadata, refusing the materialized-view type.

        TiDB has no materialized views. Without this guard the inherited lookup
        queries ``information_schema.MATERIALIZED_VIEWS`` and surfaces a raw
        ``1146 Table doesn't exist`` that reads like a broken connector.
        """
        if table_type == "mv":
            raise DatusDbException(
                ErrorCode.COMMON_FIELD_INVALID,
                "TiDB has no materialized views; use a view or a TiFlash replica instead",
            )
        return super()._get_metadata(table_type, catalog_name, database_name)

    # ==================== TiFlash (Columnar Replicas) ====================

    def get_tiflash_replicas(self, database_name: str = "") -> List[Dict[str, Any]]:
        """Return the TiFlash columnar replica state of each replicated table.

        TiFlash is TiDB's columnar engine: a table only reaches it once a
        replica is granted (``ALTER TABLE ... SET TIFLASH REPLICA n``) and
        finishes syncing. Analytical scans and MPP push-down are available only
        for tables listed here with ``available`` true, so callers planning a
        heavy aggregation can check first rather than fall back to a full row
        scan on TiKV.

        Args:
            database_name: Restrict to one database; empty means every database

        Returns:
            One dict per replicated table: database_name, table_name,
            replica_count, available, progress
        """
        self.connect()
        database_name = database_name or self.database_name

        sql = (
            "SELECT TABLE_SCHEMA, TABLE_NAME, REPLICA_COUNT, AVAILABLE, PROGRESS "
            "FROM information_schema.TIFLASH_REPLICA"
        )
        if database_name:
            safe_db = database_name.replace("'", "''")
            sql += f" WHERE TABLE_SCHEMA = '{safe_db}'"

        rows = self._execute_pandas(sql)
        return [
            {
                "database_name": rows["TABLE_SCHEMA"][i],
                "table_name": rows["TABLE_NAME"][i],
                "replica_count": int(rows["REPLICA_COUNT"][i]),
                "available": bool(rows["AVAILABLE"][i]),
                "progress": float(rows["PROGRESS"][i]),
            }
            for i in range(len(rows))
        ]

    # ==================== Utility Methods ====================

    def get_type(self) -> str:
        """Return the database type."""
        return "tidb"

    def to_dict(self) -> Dict[str, Any]:
        """Convert connector to serializable dictionary."""
        return {
            "db_type": "tidb",
            "host": self.host,
            "port": self.port,
            "user": self.username,
            "database": self.database_name,
        }

    # ==================== MigrationTargetMixin ====================

    @override
    def describe_migration_capabilities(self) -> Dict[str, Any]:
        """Describe TiDB-specific DDL and type-mapping requirements."""
        return {
            "supported": True,
            "dialect_family": "mysql-like",
            "requires": [],  # Distributed OLTP — no distribution/partition clause required
            "forbids": [
                "DUPLICATE KEY / AGGREGATE KEY (StarRocks and Doris only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks and Doris only)",
                "CHECK constraints (parsed but not enforced unless tidb_enable_check_constraint=ON)",
                "FULLTEXT indexes (parsed, then silently dropped)",
            ],
            "type_hints": {
                "HUGEINT": "DECIMAL(38,0) (TiDB has no HUGEINT)",
                "LARGEINT": "DECIMAL(38,0)",
                "unbounded VARCHAR": "VARCHAR(255) for indexed columns, TEXT otherwise",
                "BOOLEAN": "TINYINT(1)",
                "TIMESTAMP": "DATETIME (TiDB TIMESTAMP carries the same 2038 limit as MySQL)",
                "monotonic BIGINT primary key": "BIGINT AUTO_RANDOM to avoid a single-region write hotspot",
            },
            "example_ddl": (
                "CREATE TABLE db.t (\n"
                "  id BIGINT AUTO_RANDOM PRIMARY KEY,\n"
                "  name VARCHAR(255),\n"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP\n"
                ")"
            ),
        }

    @override
    def validate_ddl(self, ddl: str) -> List[str]:
        """Return TiDB compatibility errors for a proposed table DDL.

        Beyond the inherited MySQL checks this flags the two constructs TiDB
        accepts without honouring — the failure mode nobody notices, because no
        error is ever raised.
        """
        from datus_db_core.sql_utils import mask_sql_quoted_regions, strip_sql_comments

        errors = super().validate_ddl(ddl)
        upper = mask_sql_quoted_regions(strip_sql_comments(ddl)).upper()

        if re.search(r"\bCHECK\s*\(", upper):
            errors.append(
                "TiDB parses CHECK constraints but does not enforce them unless "
                "tidb_enable_check_constraint is ON; enforce the invariant in the application "
                "or enable the variable explicitly"
            )
        if re.search(r"\bFULLTEXT\b", upper):
            errors.append("TiDB accepts FULLTEXT index syntax and then drops the index; MATCH ... AGAINST is rejected")
        if re.search(r"\bAGGREGATE\s+KEY\b", upper):
            errors.append("AGGREGATE KEY is StarRocks/Doris syntax; TiDB uses PRIMARY KEY / UNIQUE KEY")

        return errors

    @override
    def suggest_table_layout(self, columns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """TiDB is a distributed OLTP store — no distribution keys to choose."""
        return {}

    @override
    def map_source_type(self, source_dialect: str, source_type: str) -> Optional[str]:
        """Map a source type when TiDB requires a dialect-specific override."""
        base = re.sub(r"\(.*\)", "", source_type.strip().upper()).strip()
        overrides = {
            "HUGEINT": "DECIMAL(38,0)",
            "LARGEINT": "DECIMAL(38,0)",
        }
        return overrides.get(base)
