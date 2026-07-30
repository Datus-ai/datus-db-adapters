# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import DorisConfig
from .connector import DorisConnector

__version__ = "0.1.0"
__all__ = ["DorisConnector", "DorisConfig", "register"]


def register():
    """Register Doris connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "doris",
        DorisConnector,
        config_class=DorisConfig,
        capabilities={"catalog", "database"},
    )
