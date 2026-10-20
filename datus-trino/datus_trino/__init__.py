# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import TrinoConfig
from .connector import TrinoConnector

__version__ = "0.1.4"
__all__ = ["TrinoConnector", "TrinoConfig", "register"]


def register():
    """Register Trino connector with Datus registry."""
    from datus_db_core import connector_registry

    from .handlers import build_trino_uri, parse_trino_identifier, resolve_trino_context

    connector_registry.register(
        "trino",
        TrinoConnector,
        config_class=TrinoConfig,
        capabilities={"catalog", "schema"},
        uri_builder=build_trino_uri,
        context_resolver=resolve_trino_context,
        identifier_parser=parse_trino_identifier,
    )
