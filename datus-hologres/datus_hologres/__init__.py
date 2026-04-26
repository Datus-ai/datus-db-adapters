# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import HologresConfig
from .connector import HologresConnector

__version__ = "0.1.0"
__all__ = ["HologresConnector", "HologresConfig", "register"]


def register():
    """Register Hologres connector with Datus registry."""
    from datus_db_core import connector_registry

    from .handlers import build_hologres_uri, resolve_hologres_context

    connector_registry.register(
        "hologres",
        HologresConnector,
        config_class=HologresConfig,
        capabilities={"database", "schema"},
        uri_builder=build_hologres_uri,
        context_resolver=resolve_hologres_context,
    )
