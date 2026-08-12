# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import GaussDBConfig
from .connector import GaussDBConnector
from .handlers import (
    build_gaussdb_uri,
    parse_gaussdb_identifier,
    resolve_gaussdb_context,
)
from .skills import get_gaussdb_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["GaussDBConnector", "GaussDBConfig", "register"]


def register():
    """Register GaussDB and its generic Agent integration hooks."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "gaussdb",
        GaussDBConnector,
        config_class=GaussDBConfig,
        display_name="GaussDB",
        capabilities={"database", "schema"},
        uri_builder=build_gaussdb_uri,
        context_resolver=resolve_gaussdb_context,
        parser_dialect="postgres",
        identifier_parser=parse_gaussdb_identifier,
        sql_generation_notes=get_gaussdb_sql_generation_notes,
    )
