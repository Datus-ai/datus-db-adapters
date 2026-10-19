# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from .config import HanaConfig
from .connector import HanaConnector

__version__ = "0.1.0"
__all__ = ["HanaConfig", "HanaConnector", "register"]


def register():
    from datus_db_core import connector_registry

    if not callable(getattr(connector_registry, "get_sql_analyzer", None)):
        raise RuntimeError("HANA requires db-core >= 0.1.8 loaded at startup; upgrade and restart Datus")

    from .analysis import analyze_hana_sql
    from .dialect_operations import HanaDialectOperations
    from .handlers import build_hana_uri, parse_hana_identifier, resolve_hana_context

    connector_registry.register(
        "hana",
        HanaConnector,
        config_class=HanaConfig,
        display_name="SAP HANA",
        capabilities={"schema"},
        uri_builder=build_hana_uri,
        context_resolver=resolve_hana_context,
        parser_dialect="postgres",
        identifier_parser=parse_hana_identifier,
        dialect_operations=HanaDialectOperations(),
        sql_analyzer=analyze_hana_sql,
        sql_generation_notes="Use verified common SELECT SQL with double-quoted schema.table identifiers. HANA folds unquoted names to uppercase. Execute original SQL; local SQL analysis does not support SQLScript, Calculation View parameters, or arbitrary HANA grammar. Use LIMIT; literal queries use FROM DUMMY. Tenant is a connection target, not an SQL namespace.",
    )
