# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points

from datus_db_core import connector_registry
from datus_dws import DWSConfig, DWSConnector, register


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

        metadata = connector_registry.get_metadata("dws")
        assert metadata is not None
        assert metadata.connector_class is DWSConnector
        assert metadata.config_class is DWSConfig
        assert metadata.display_name == "Huawei DWS"
        assert metadata.parser_dialect == "postgres"
        # Without these, Datus resolves a database name as a schema.
        assert connector_registry.get_capabilities("dws") == {"database", "schema"}
        assert connector_registry.get_identifier_parser("dws") is not None
        assert connector_registry.get_uri_builder("dws") is not None
        assert connector_registry.get_context_resolver("dws") is not None
        notes = connector_registry.get_sql_generation_notes("dws")
        assert callable(notes)
        assert notes().startswith("# DWS SQL")
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


def test_adapter_entry_point_is_declared():
    adapters = {ep.name: ep for ep in entry_points(group="datus.adapters")}

    assert "dws" in adapters
    assert adapters["dws"].load() is register


def test_skills_entry_point_is_declared():
    skills = {ep.name: ep for ep in entry_points(group="datus.skills")}

    assert "dws" in skills
    assert skills["dws"].load()().endswith("skills")


def test_no_sqlalchemy_dialect_is_registered():
    """DWS answers standard MD5 auth, so it needs no dialect of its own.

    Declaring one would mean owning a driver surface that the psycopg2 path in
    datus-postgresql already covers.
    """
    dialects = {ep.name for ep in entry_points(group="sqlalchemy.dialects")}

    assert not any(name == "dws" or name.startswith("dws.") for name in dialects)
