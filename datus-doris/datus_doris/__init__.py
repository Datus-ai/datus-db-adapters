# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import DorisConfig
from .connector import DorisConnector
from .handlers import build_doris_uri, parse_doris_identifier, resolve_doris_context
from .skills import get_doris_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["DorisConnector", "DorisConfig", "register"]


def register():
    """Register Doris and its generic Agent integration hooks."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "doris",
        DorisConnector,
        config_class=DorisConfig,
        display_name="Apache Doris",
        capabilities={"catalog", "database"},
        uri_builder=build_doris_uri,
        context_resolver=resolve_doris_context,
        parser_dialect="doris",
        identifier_parser=parse_doris_identifier,
        sql_generation_notes=get_doris_sql_generation_notes,
    )
