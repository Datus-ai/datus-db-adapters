# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import DWSConfig
from .connector import DWSConnector
from .handlers import (
    build_dws_uri,
    parse_dws_identifier,
    resolve_dws_context,
)
from .skills import get_dws_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["DWSConnector", "DWSConfig", "register"]


def register():
    """Register DWS and its generic Agent integration hooks."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "dws",
        DWSConnector,
        config_class=DWSConfig,
        display_name="Huawei DWS",
        capabilities={"database", "schema"},
        uri_builder=build_dws_uri,
        context_resolver=resolve_dws_context,
        parser_dialect="postgres",
        identifier_parser=parse_dws_identifier,
        sql_generation_notes=get_dws_sql_generation_notes,
    )
