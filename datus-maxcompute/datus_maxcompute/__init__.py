# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import MaxComputeConfig
from .connector import MaxComputeConnector
from .handlers import (
    MAXCOMPUTE_SQL_GENERATION_NOTES,
    build_maxcompute_uri,
    parse_maxcompute_identifier,
    resolve_maxcompute_context,
)

__version__ = "0.1.0"
__all__ = ["MaxComputeConnector", "MaxComputeConfig", "register"]


def register():
    """Register the MaxCompute connector and its generic integration hooks."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "maxcompute",
        MaxComputeConnector,
        config_class=MaxComputeConfig,
        capabilities={"database", "schema"},
        uri_builder=build_maxcompute_uri,
        context_resolver=resolve_maxcompute_context,
        parser_dialect="hive",
        identifier_parser=parse_maxcompute_identifier,
        sql_generation_notes=MAXCOMPUTE_SQL_GENERATION_NOTES,
    )
