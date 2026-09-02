# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the TiDB adapter.

The registry assertions previously lived inside ``test_connector_unit.py``; this file owns them
together with the ``datus.adapters`` entry point check. A typo in that entry point leaves the
adapter silently undiscoverable while every other test keeps passing.
"""

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_tidb import TiDBConfig, TiDBConnector, register
from datus_tidb.skills import get_tidb_sql_generation_notes

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

        metadata = connector_registry.get_metadata("tidb")
        assert metadata is not None
        assert metadata.connector_class is TiDBConnector
        assert metadata.config_class is TiDBConfig
        # Explicit display_name; the db_type.capitalize() fallback would render "Tidb".
        assert metadata.display_name == "TiDB"
        # sqlglot has no TiDB dialect; TiDB's SQL surface parses as MySQL.
        assert metadata.parser_dialect == "mysql"

        # TiDB is MySQL-compatible: database.table addressing, no catalog or schema level.
        assert connector_registry.get_capabilities("tidb") == {"database"}
        assert connector_registry.support_database("tidb") is True
        assert connector_registry.support_catalog("tidb") is False
        assert connector_registry.support_schema("tidb") is False

        # Only the skills hook is wired; datus-tidb ships no handlers.py.
        assert connector_registry.get_sql_generation_notes("tidb") is get_tidb_sql_generation_notes
        assert connector_registry.get_uri_builder("tidb") is None
        assert connector_registry.get_context_resolver("tidb") is None
        assert connector_registry.get_identifier_parser("tidb") is None
        # No factory: the registry constructs TiDBConnector(config) directly.
        assert "tidb" not in connector_registry._factories
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "tidb"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is TiDBConnector
    finally:
        _restore(saved)
