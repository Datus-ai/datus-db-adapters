# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Registration contract for the ClickZetta adapter.

Nothing else in the suite exercises ``register()`` or the ``datus.adapters`` entry point, so a
typo in either would leave the adapter silently undiscoverable while every other test still passes.

ClickZetta is the one standalone adapter: its connector takes keyword arguments instead of a
config object, so ``register()`` also installs a factory that flattens the config into kwargs.
"""

from importlib.metadata import entry_points

import datus_clickzetta
from datus_clickzetta import ClickZettaConfig, ClickZettaConnector, register
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

        metadata = connector_registry.get_metadata("clickzetta")
        assert metadata is not None
        assert metadata.connector_class is ClickZettaConnector
        assert metadata.config_class is ClickZettaConfig
        # register() passes no display_name, so AdapterMetadata falls back to db_type.capitalize().
        assert metadata.display_name == "Clickzetta"
        # No parser_dialect override; sqlglot has no ClickZetta dialect.
        assert metadata.parser_dialect is None

        # ClickZetta addresses objects as schema.table inside a workspace -- no catalog level.
        assert connector_registry.get_capabilities("clickzetta") == {"database", "schema"}
        assert connector_registry.support_database("clickzetta") is True
        assert connector_registry.support_schema("clickzetta") is True
        assert connector_registry.support_catalog("clickzetta") is False

        # datus-clickzetta ships no handlers.py/skills.py; the Agent's generic handling applies.
        assert connector_registry.get_uri_builder("clickzetta") is None
        assert connector_registry.get_context_resolver("clickzetta") is None
        assert connector_registry.get_identifier_parser("clickzetta") is None
        assert connector_registry.get_sql_generation_notes("clickzetta") is None
    finally:
        _restore(saved)


def test_registration_installs_a_factory_that_flattens_config_into_kwargs(monkeypatch):
    """Unlike the SQLAlchemy adapters, ClickZettaConnector cannot be built as ``cls(config)``."""
    captured = {}

    class StubConnector:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    saved = _snapshot()
    try:
        register()

        assert "clickzetta" in connector_registry._factories

        monkeypatch.setattr(datus_clickzetta, "ClickZettaConnector", StubConnector)
        connector = connector_registry.create_connector(
            "clickzetta",
            {
                "service": "svc.example.com",
                "username": "user",
                "password": "secret",
                "instance": "inst",
                "workspace": "ws",
            },
        )

        assert isinstance(connector, StubConnector)
        assert captured == {
            "service": "svc.example.com",
            "username": "user",
            "password": "secret",
            "instance": "inst",
            "workspace": "ws",
            # Defaults the factory supplies when the config omits them.
            "schema": "PUBLIC",
            "vcluster": "DEFAULT_AP",
            "secure": None,
            "hints": None,
            "extra": None,
        }
    finally:
        _restore(saved)


def test_entry_point_is_discoverable_and_registers_under_its_own_name():
    """``discover_adapters()`` keys the registry by entry point name, so the two must agree."""
    saved = _snapshot()
    try:
        matches = [ep for ep in entry_points(group="datus.adapters") if ep.name == "clickzetta"]
        assert len(matches) == 1

        entry_point = matches[0]
        assert entry_point.load() is register

        entry_point.load()()
        assert connector_registry.is_registered(entry_point.name)
        assert connector_registry.get_metadata(entry_point.name).connector_class is ClickZettaConnector
    finally:
        _restore(saved)
