# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the ClickHouse adapter.

Nothing else in the suite exercises ``register()`` or the ``datus.adapters`` entry point, so a
typo in either would leave the adapter silently undiscoverable while every other test still passes.
"""

from importlib.metadata import entry_points

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector, register
from datus_clickhouse.handlers import build_clickhouse_uri, resolve_clickhouse_context
from datus_db_core import connector_registry

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

        metadata = connector_registry.get_metadata("clickhouse")
        assert metadata is not None
        assert metadata.connector_class is ClickHouseConnector
        assert metadata.config_class is ClickHouseConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Clickhouse"
        # No parser_dialect override: sqlglot's dialect name already matches the db_type.
        assert metadata.parser_dialect is None

        # ClickHouse addresses objects as database.table -- it has neither a catalog nor a schema level.
        assert connector_registry.get_capabilities("clickhouse") == {"database"}
        assert connector_registry.support_database("clickhouse") is True
        assert connector_registry.support_catalog("clickhouse") is False
        assert connector_registry.support_schema("clickhouse") is False

        assert connector_registry.get_uri_builder("clickhouse") is build_clickhouse_uri
        assert connector_registry.get_context_resolver("clickhouse") is resolve_clickhouse_context
        # datus-clickhouse ships no identifier parser or skills module.
        assert connector_registry.get_identifier_parser("clickhouse") is None
        assert connector_registry.get_sql_generation_notes("clickhouse") is None
        # No factory: the registry constructs ClickHouseConnector(config) directly.
        assert "clickhouse" not in connector_registry._factories
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "clickhouse"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is ClickHouseConnector
    finally:
        _restore(saved)
