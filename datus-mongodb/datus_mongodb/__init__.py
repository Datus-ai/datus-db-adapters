# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from datus_db_core import connector_registry

from .connector import MongoDBConfig, MongoDBConnector

__all__ = ["MongoDBConfig", "MongoDBConnector", "register"]


def register() -> None:
    connector_registry.register(
        "mongodb",
        MongoDBConnector,
        config_class=MongoDBConfig,
        display_name="MongoDB",
        capabilities={"database"},
        parser_dialect="postgres",
    )
