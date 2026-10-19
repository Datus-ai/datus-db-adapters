# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector, register
from datus_clickhouse.handlers import build_clickhouse_uri, resolve_clickhouse_context
from datus_db_core import connector_registry


def test_registered_config_hooks_and_entry_point():
    attributes = ("_connectors", "_factories", "_metadata", "_capabilities", "_uri_builders", "_context_resolvers")
    initialized = connector_registry._initialized
    saved = {name: dict(getattr(connector_registry, name)) for name in attributes}
    try:
        register()
        metadata = connector_registry.get_metadata("clickhouse")
        assert metadata.config_class is ClickHouseConfig
        assert connector_registry.get_capabilities("clickhouse") == {"database"}
        assert connector_registry.get_uri_builder("clickhouse") is build_clickhouse_uri
        assert connector_registry.get_context_resolver("clickhouse") is resolve_clickhouse_context
        connector = connector_registry.create_connector("clickhouse", {"username": "u", "protocol": "https"})
        try:
            assert type(connector) is ClickHouseConnector
            assert connector.config.port == 8443
        finally:
            connector.close()
        assert entry_points(group="datus.adapters")["clickhouse"].load() is register
    finally:
        connector_registry._initialized = initialized
        for name, value in saved.items():
            getattr(connector_registry, name).clear()
            getattr(connector_registry, name).update(value)
