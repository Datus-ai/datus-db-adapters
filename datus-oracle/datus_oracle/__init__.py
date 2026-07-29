# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from .config import OracleConfig
from .connector import OracleConnector
from .dialect_operations import OracleDialectOperations

__version__ = "0.1.0"
__all__ = ["OracleConnector", "OracleConfig", "OracleDialectOperations", "register"]


def register():
    """Register Oracle connector with Datus registry."""
    from datus_db_core import connector_registry

    from .handlers import build_oracle_uri, resolve_oracle_context

    connector_registry.register(
        "oracle",
        OracleConnector,
        config_class=OracleConfig,
        capabilities={"schema"},
        uri_builder=build_oracle_uri,
        context_resolver=resolve_oracle_context,
        parser_dialect="oracle",
        dialect_operations=OracleDialectOperations(),
    )
