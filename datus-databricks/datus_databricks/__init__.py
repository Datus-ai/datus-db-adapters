# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import DatabricksConfig
from .connector import DatabricksConnector
from .handlers import build_databricks_uri, parse_databricks_identifier, resolve_databricks_context

__version__ = "0.1.0"
__all__ = ["DatabricksConnector", "DatabricksConfig", "register"]


def register():
    """Register the Databricks SQL warehouse connector with Datus."""
    from datus_db_core import connector_registry

    connector_registry.register(
        "databricks",
        DatabricksConnector,
        config_class=DatabricksConfig,
        display_name="Databricks SQL",
        capabilities={"catalog", "database"},
        uri_builder=build_databricks_uri,
        context_resolver=resolve_databricks_context,
        parser_dialect="databricks",
        identifier_parser=parse_databricks_identifier,
    )
