# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import StarRocksConfig
from .connector import StarRocksConnector
from .skills import get_starrocks_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["StarRocksConnector", "StarRocksConfig", "register"]


def register():
    """Register StarRocks connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "starrocks",
        StarRocksConnector,
        config_class=StarRocksConfig,
        capabilities={"catalog", "database"},
        sql_generation_notes=get_starrocks_sql_generation_notes,
    )
