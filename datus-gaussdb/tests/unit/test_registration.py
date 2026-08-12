# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_db_core import connector_registry
from datus_gaussdb import GaussDBConfig, GaussDBConnector, register


@pytest.fixture
def restore_registry():
    """Register into the process-wide registry without leaking into other tests."""
    names = ("connectors", "metadata", "capabilities", "uri_builders", "context_resolvers")
    saved = {name: getattr(connector_registry, f"_{name}").copy() for name in names}
    try:
        yield
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


@pytest.mark.acceptance
def test_registration_exposes_generic_agent_hooks(restore_registry):
    """register() must wire up everything the Agent needs to address GaussDB."""
    register()

    metadata = connector_registry.get_metadata("gaussdb")
    assert metadata is not None
    assert metadata.connector_class is GaussDBConnector
    assert metadata.config_class is GaussDBConfig
    assert metadata.display_name == "GaussDB"
    assert metadata.parser_dialect == "postgres"

    assert connector_registry.get_capabilities("gaussdb") == {"database", "schema"}
    assert connector_registry.get_uri_builder("gaussdb") is not None
    assert connector_registry.get_context_resolver("gaussdb") is not None
    assert connector_registry.get_identifier_parser("gaussdb") is not None
    notes = connector_registry.get_sql_generation_notes("gaussdb")
    assert callable(notes)
    assert "# GaussDB SQL" in notes()


@pytest.mark.acceptance
def test_registered_hooks_are_the_public_handlers(restore_registry):
    """The registered callables are the same ones the handlers module exports."""
    from datus_gaussdb.handlers import (
        build_gaussdb_uri,
        parse_gaussdb_identifier,
        resolve_gaussdb_context,
    )

    register()

    assert connector_registry.get_uri_builder("gaussdb") is build_gaussdb_uri
    assert connector_registry.get_context_resolver("gaussdb") is resolve_gaussdb_context
    assert connector_registry.get_identifier_parser("gaussdb") is parse_gaussdb_identifier


@pytest.mark.acceptance
def test_registration_has_no_catalog_capability(restore_registry):
    """GaussDB addressing is database.schema.table — there is no catalog level."""
    register()

    assert connector_registry.support_catalog("gaussdb") is False
    assert connector_registry.support_database("gaussdb") is True
    assert connector_registry.support_schema("gaussdb") is True
