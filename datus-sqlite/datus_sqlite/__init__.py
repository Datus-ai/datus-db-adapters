# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
from .config import SQLiteConfig
from .connector import SQLiteConnector

__all__ = ["SQLiteConfig", "SQLiteConnector", "register"]


def register():
    from datus_db_core import connector_registry

    connector_registry.register(
        "sqlite",
        SQLiteConnector,
        config_class=SQLiteConfig,
        display_name="SQLite",
        capabilities={"database"},
    )
