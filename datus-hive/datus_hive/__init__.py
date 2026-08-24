# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import HiveConfig
from .connector import HiveConnector

__version__ = "0.1.0"
__all__ = ["HiveConnector", "HiveConfig", "register"]


def register():
    """Register Hive connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register("hive", HiveConnector, config_class=HiveConfig, capabilities={"database"})
