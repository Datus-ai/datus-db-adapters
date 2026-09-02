# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the Trino adapter.

Nothing else in the suite exercises ``register()`` or the ``datus.adapters`` entry point, so a
typo in either would leave the adapter silently undiscoverable while every other test still passes.
"""

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_trino import TrinoConfig, TrinoConnector, register

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

        metadata = connector_registry.get_metadata("trino")
        assert metadata is not None
        assert metadata.connector_class is TrinoConnector
        assert metadata.config_class is TrinoConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Trino"
        # No parser_dialect override: sqlglot's dialect name already matches the db_type.
        assert metadata.parser_dialect is None

        # Trino addresses objects as catalog.schema.table -- the middle level is a schema, not a database.
        assert connector_registry.get_capabilities("trino") == {"catalog", "schema"}
        assert connector_registry.support_catalog("trino") is True
        assert connector_registry.support_schema("trino") is True
        assert connector_registry.support_database("trino") is False

        # datus-trino ships no handlers.py/skills.py; the Agent's generic handling applies.
        assert connector_registry.get_uri_builder("trino") is None
        assert connector_registry.get_context_resolver("trino") is None
        assert connector_registry.get_identifier_parser("trino") is None
        assert connector_registry.get_sql_generation_notes("trino") is None
        # No factory: the registry constructs TrinoConnector(config) directly.
        assert "trino" not in connector_registry._factories
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "trino"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is TrinoConnector
    finally:
        _restore(saved)
