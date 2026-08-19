# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from datus_db_core import connector_registry
from datus_doris import DorisConfig, DorisConnector, register
from datus_doris.handlers import build_doris_uri, parse_doris_identifier, resolve_doris_context


def test_registration_exposes_generic_agent_hooks():
    saved = {
        "connectors": connector_registry._connectors.copy(),
        "metadata": connector_registry._metadata.copy(),
        "capabilities": connector_registry._capabilities.copy(),
        "uri_builders": connector_registry._uri_builders.copy(),
        "context_resolvers": connector_registry._context_resolvers.copy(),
    }
    try:
        register()

        metadata = connector_registry.get_metadata("doris")
        assert metadata is not None
        assert metadata.connector_class is DorisConnector
        assert metadata.config_class is DorisConfig
        assert metadata.display_name == "Apache Doris"
        assert metadata.parser_dialect == "doris"

        # Doris addresses objects as catalog.database.table and has no schema level.
        assert connector_registry.get_capabilities("doris") == {"catalog", "database"}

        assert connector_registry.get_uri_builder("doris") is build_doris_uri
        assert connector_registry.get_context_resolver("doris") is resolve_doris_context
        assert connector_registry.get_identifier_parser("doris") is parse_doris_identifier

        notes = connector_registry.get_sql_generation_notes("doris")
        assert callable(notes)
        assert notes().startswith("# Apache Doris SQL")
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
