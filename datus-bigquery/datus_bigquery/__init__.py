# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import BigQueryConfig
from .connector import BigQueryConnector
from .handlers import build_bigquery_uri, parse_bigquery_identifier, resolve_bigquery_context
from .skills import get_bigquery_sql_generation_notes

__version__ = "0.1.0"
__all__ = ["BigQueryConnector", "BigQueryConfig", "register"]


def register():
    """Register BigQuery connector with Datus registry."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "bigquery",
        BigQueryConnector,
        config_class=BigQueryConfig,
        display_name="Google BigQuery",
        capabilities={"catalog", "database"},
        uri_builder=build_bigquery_uri,
        context_resolver=resolve_bigquery_context,
        parser_dialect="bigquery",
        identifier_parser=parse_bigquery_identifier,
        sql_generation_notes=get_bigquery_sql_generation_notes,
    )
