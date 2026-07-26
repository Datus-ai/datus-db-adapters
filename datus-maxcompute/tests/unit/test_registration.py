# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from datus_db_core.registry import ConnectorRegistry
from datus_maxcompute import register


def test_registration_exposes_generic_hooks():
    saved_connectors = ConnectorRegistry._connectors.copy()
    saved_metadata = ConnectorRegistry._metadata.copy()
    saved_capabilities = ConnectorRegistry._capabilities.copy()
    saved_uri_builders = ConnectorRegistry._uri_builders.copy()
    saved_context_resolvers = ConnectorRegistry._context_resolvers.copy()
    try:
        register()
        assert ConnectorRegistry.get_capabilities("maxcompute") == {"database", "schema"}
        assert ConnectorRegistry.get_parser_dialect("maxcompute") == "hive"
        assert ConnectorRegistry.get_identifier_parser("maxcompute") is not None
        assert "project.schema.table" in ConnectorRegistry.get_sql_generation_notes("maxcompute")
        assert ConnectorRegistry.get_uri_builder("maxcompute") is not None
        assert ConnectorRegistry.get_context_resolver("maxcompute") is not None
    finally:
        ConnectorRegistry._connectors = saved_connectors
        ConnectorRegistry._metadata = saved_metadata
        ConnectorRegistry._capabilities = saved_capabilities
        ConnectorRegistry._uri_builders = saved_uri_builders
        ConnectorRegistry._context_resolvers = saved_context_resolvers


def test_register_handlers_updates_adapter_metadata_hooks():
    saved_connectors = ConnectorRegistry._connectors.copy()
    saved_metadata = ConnectorRegistry._metadata.copy()
    saved_capabilities = ConnectorRegistry._capabilities.copy()
    saved_uri_builders = ConnectorRegistry._uri_builders.copy()
    saved_context_resolvers = ConnectorRegistry._context_resolvers.copy()

    def parse_identifier(value):
        return {"table_name": value}

    try:
        register()
        ConnectorRegistry.register_handlers(
            "maxcompute",
            parser_dialect="odps",
            identifier_parser=parse_identifier,
            sql_generation_notes="custom notes",
        )

        assert ConnectorRegistry.get_parser_dialect("maxcompute") == "odps"
        assert ConnectorRegistry.get_identifier_parser("maxcompute") is parse_identifier
        assert ConnectorRegistry.get_sql_generation_notes("maxcompute") == "custom notes"
    finally:
        ConnectorRegistry._connectors = saved_connectors
        ConnectorRegistry._metadata = saved_metadata
        ConnectorRegistry._capabilities = saved_capabilities
        ConnectorRegistry._uri_builders = saved_uri_builders
        ConnectorRegistry._context_resolvers = saved_context_resolvers
