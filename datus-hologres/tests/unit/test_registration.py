# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from datus_db_core import connector_registry
from datus_hologres import HologresConfig, HologresConnector, register


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

        metadata = connector_registry.get_metadata("hologres")
        assert metadata is not None
        assert metadata.connector_class is HologresConnector
        assert metadata.config_class is HologresConfig
        assert metadata.parser_dialect == "postgres"
        assert connector_registry.get_capabilities("hologres") == {"database", "schema"}
        assert connector_registry.get_identifier_parser("hologres") is not None
        assert connector_registry.get_uri_builder("hologres") is not None
        assert connector_registry.get_context_resolver("hologres") is not None
        assert "Hologres SQL rules" in connector_registry.get_sql_generation_notes("hologres")
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
