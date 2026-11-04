# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
from .config import DuckDBConfig
from .connector import DuckdbConnector

__all__ = ["DuckDBConfig", "DuckdbConnector", "register"]


def register():
    from datus_db_core import connector_registry

    connector_registry.register(
        "duckdb",
        DuckdbConnector,
        config_class=DuckDBConfig,
        display_name="DuckDB",
        capabilities={"database", "schema"},
    )
