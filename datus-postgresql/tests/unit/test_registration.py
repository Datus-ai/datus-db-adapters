# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the PostgreSQL adapter.

Nothing else in the suite exercises ``register()`` or the ``datus.adapters`` entry point, so a
typo in either would leave the adapter silently undiscoverable while every other test still passes.
"""

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector, register
from datus_postgresql.handlers import build_postgresql_uri, resolve_postgresql_context

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

        metadata = connector_registry.get_metadata("postgresql")
        assert metadata is not None
        assert metadata.connector_class is PostgreSQLConnector
        assert metadata.config_class is PostgreSQLConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Postgresql"
        # No parser_dialect override: sqlglot's dialect name already matches the db_type.
        assert metadata.parser_dialect is None

        # PostgreSQL addresses objects as schema.table inside one database -- there is no catalog level.
        assert connector_registry.get_capabilities("postgresql") == {"database", "schema"}
        assert connector_registry.support_database("postgresql") is True
        assert connector_registry.support_schema("postgresql") is True
        assert connector_registry.support_catalog("postgresql") is False

        assert connector_registry.get_uri_builder("postgresql") is build_postgresql_uri
        assert connector_registry.get_context_resolver("postgresql") is resolve_postgresql_context
        # datus-postgresql ships no identifier parser or skills module.
        assert connector_registry.get_identifier_parser("postgresql") is None
        assert connector_registry.get_sql_generation_notes("postgresql") is None
        # No factory: the registry constructs PostgreSQLConnector(config) directly.
        assert "postgresql" not in connector_registry._factories
    finally:
        _restore(saved)


def test_postgres_alias_resolves_to_the_same_registration():
    """``ConnectorRegistry._DIALECT_ALIASES`` maps the common "postgres" spelling onto this adapter."""
    saved = _snapshot()
    try:
        register()

        assert connector_registry.get_metadata("postgres") is connector_registry.get_metadata("postgresql")
        assert connector_registry.get_uri_builder("postgres") is build_postgresql_uri
        assert connector_registry.get_capabilities("postgres") == {"database", "schema"}
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "postgresql"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is PostgreSQLConnector
    finally:
        _restore(saved)
