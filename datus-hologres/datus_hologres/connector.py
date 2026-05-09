# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, Union

from datus_postgresql import PostgreSQLConnector

from .config import HologresConfig


class HologresConnector(PostgreSQLConnector):
    """Hologres database connector using PostgreSQL-compatible behavior."""

    def __init__(self, config: Union[HologresConfig, dict]):
        """Initialize Hologres connector.

        Args:
            config: HologresConfig object or dict with configuration
        """
        if isinstance(config, dict):
            config = HologresConfig(**config)
        elif not isinstance(config, HologresConfig):
            raise TypeError(f"config must be HologresConfig or dict, got {type(config)}")

        self.hologres_config = config
        super().__init__(config)
        self.db_type = "hologres"
        self.adapter_type = "hologres"

    def describe_migration_capabilities(self) -> Dict[str, Any]:
        return {
            "supported": True,
            "dialect_family": "postgres-like",
            "requires": [],
            "forbids": [
                "DUPLICATE KEY (StarRocks-only)",
                "DISTRIBUTED BY HASH ... BUCKETS (StarRocks-only)",
                "ENGINE = (MySQL/ClickHouse syntax)",
            ],
            "type_hints": {
                "HUGEINT": "NUMERIC(38,0) (PostgreSQL-compatible fallback)",
                "LARGEINT": "NUMERIC(38,0)",
                "unbounded VARCHAR": "TEXT (PostgreSQL-compatible fallback)",
                "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
            },
            "limitations": [
                "Hologres distribution-oriented table layout syntax is not optimized by this skeleton.",
                "Hologres-specific storage options are not optimized by this skeleton.",
                "Hologres external table syntax is treated as normal SQL-accessible metadata for now.",
            ],
            "notes": [
                "Hologres is PostgreSQL-compatible and this adapter currently reuses PostgreSQL behavior.",
                "Hologres-specific distribution, storage, and external table DDL optimization is not implemented yet.",
            ],
            "example_ddl": (
                "CREATE TABLE public.t (\n  id BIGINT NOT NULL,\n  name TEXT,\n  created_at TIMESTAMPTZ\n)"
            ),
        }
