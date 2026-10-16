# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""AWS Athena adapter for Datus."""

from .config import AthenaConfig
from .connector import AthenaConnector
from .handlers import (
    build_athena_uri,
    get_athena_sql_generation_notes,
    parse_athena_identifier,
    resolve_athena_context,
)
from .models import AthenaExecuteSQLResult

__version__ = "0.1.0"
__all__ = ["AthenaConfig", "AthenaConnector", "AthenaExecuteSQLResult", "register"]


def register():
    from datus_db_core import connector_registry

    connector_registry.register(
        "athena",
        AthenaConnector,
        config_class=AthenaConfig,
        display_name="Amazon Athena",
        capabilities={"catalog", "database"},
        uri_builder=build_athena_uri,
        context_resolver=resolve_athena_context,
        parser_dialect="athena",
        identifier_parser=parse_athena_identifier,
        sql_generation_notes=get_athena_sql_generation_notes,
    )
