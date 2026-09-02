# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the Oracle adapter.

``test_connector_unit.py`` checks a few registry lookups in passing; this file owns the full
contract, including hook identity and the ``datus.adapters`` entry point. A typo in that entry
point leaves the adapter silently undiscoverable while every other test keeps passing.
"""

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_oracle import OracleConfig, OracleConnector, OracleDialectOperations, register
from datus_oracle.handlers import build_oracle_uri, resolve_oracle_context
from datus_oracle.skills import get_oracle_sql_generation_notes

# Every mutable registry mapping ``register()`` can touch, so tests cannot leak into each other.
_REGISTRY_DICTS = (
    "connectors",
    "factories",
    "metadata",
    "capabilities",
    "uri_builders",
    "context_resolvers",
)


def _snapshot():
    return {attr: getattr(connector_registry, f"_{attr}").copy() for attr in _REGISTRY_DICTS}


def _restore(saved):
    for attr, values in saved.items():
        target = getattr(connector_registry, f"_{attr}")
        target.clear()
        target.update(values)


def test_registration_exposes_current_adapter_contract():
    saved = _snapshot()
    try:
        register()

        metadata = connector_registry.get_metadata("oracle")
        assert metadata is not None
        assert metadata.connector_class is OracleConnector
        assert metadata.config_class is OracleConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Oracle"
        assert metadata.parser_dialect == "oracle"

        # An Oracle service is a single database; users are schemas, so only schema.table applies.
        assert connector_registry.get_capabilities("oracle") == {"schema"}
        assert connector_registry.support_schema("oracle") is True
        assert connector_registry.support_catalog("oracle") is False
        assert connector_registry.support_database("oracle") is False

        assert connector_registry.get_uri_builder("oracle") is build_oracle_uri
        assert connector_registry.get_context_resolver("oracle") is resolve_oracle_context
        assert connector_registry.get_sql_generation_notes("oracle") is get_oracle_sql_generation_notes
        # datus-oracle ships no identifier parser; sqlglot's oracle dialect handles parsing.
        assert connector_registry.get_identifier_parser("oracle") is None

        # Oracle is the only adapter registering dialect_operations, and it registers an instance.
        assert isinstance(connector_registry.get_dialect_operations("oracle"), OracleDialectOperations)
        # No factory: the registry constructs OracleConnector(config) directly.
        assert "oracle" not in connector_registry._factories
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "oracle"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is OracleConnector
    finally:
        _restore(saved)
