# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the StarRocks adapter.

``test_skills.py`` only checks the SQL-notes hook, so this file owns the rest of the contract:
the full registry metadata plus the ``datus.adapters`` entry point. A typo in that entry point
leaves the adapter silently undiscoverable while every other test keeps passing.
"""

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_starrocks import StarRocksConfig, StarRocksConnector, register
from datus_starrocks.skills import get_starrocks_sql_generation_notes

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

        metadata = connector_registry.get_metadata("starrocks")
        assert metadata is not None
        assert metadata.connector_class is StarRocksConnector
        assert metadata.config_class is StarRocksConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Starrocks"
        # No parser_dialect override: sqlglot's dialect name already matches the db_type.
        assert metadata.parser_dialect is None

        # StarRocks addresses objects as catalog.database.table and has no schema level.
        assert connector_registry.get_capabilities("starrocks") == {"catalog", "database"}
        assert connector_registry.support_catalog("starrocks") is True
        assert connector_registry.support_database("starrocks") is True
        assert connector_registry.support_schema("starrocks") is False

        # Only the skills hook is wired; datus-starrocks ships no handlers.py.
        assert connector_registry.get_sql_generation_notes("starrocks") is get_starrocks_sql_generation_notes
        assert connector_registry.get_uri_builder("starrocks") is None
        assert connector_registry.get_context_resolver("starrocks") is None
        assert connector_registry.get_identifier_parser("starrocks") is None
        # No factory: the registry constructs StarRocksConnector(config) directly.
        assert "starrocks" not in connector_registry._factories
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "starrocks"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is StarRocksConnector
    finally:
        _restore(saved)
