# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import ClickHouseConfig
from .connector import ClickHouseConnector

__version__ = "0.1.4"
__all__ = ["ClickHouseConnector", "ClickHouseConfig", "register"]


def register():
    """Register ClickHouse connector with Datus registry."""
    from datus_db_core import connector_registry

    from .handlers import build_clickhouse_uri, resolve_clickhouse_context

    connector_registry.register(
        "clickhouse",
        ClickHouseConnector,
        config_class=ClickHouseConfig,
        capabilities={"database"},
        uri_builder=build_clickhouse_uri,
        context_resolver=resolve_clickhouse_context,
    )
