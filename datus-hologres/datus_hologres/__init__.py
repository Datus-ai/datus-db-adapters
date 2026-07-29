# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import HologresConfig
from .connector import HologresConnector
from .handlers import (
    HOLOGRES_SQL_GENERATION_NOTES,
    build_hologres_uri,
    parse_hologres_identifier,
    resolve_hologres_context,
)

__version__ = "0.1.0"
__all__ = ["HologresConnector", "HologresConfig", "register"]


def register():
    """Register Hologres and its generic Agent integration hooks."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "hologres",
        HologresConnector,
        config_class=HologresConfig,
        display_name="Hologres",
        capabilities={"database", "schema"},
        uri_builder=build_hologres_uri,
        context_resolver=resolve_hologres_context,
        parser_dialect="postgres",
        identifier_parser=parse_hologres_identifier,
        sql_generation_notes=HOLOGRES_SQL_GENERATION_NOTES,
    )
