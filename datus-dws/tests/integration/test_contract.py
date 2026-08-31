# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Adapter contract as Datus consumes it, exercised against a live cluster."""

import pytest

from datus_db_core import connector_registry
from datus_dws import DWSConfig, DWSConnector
from datus_dws.handlers import build_dws_uri, parse_dws_identifier, resolve_dws_context


@pytest.mark.integration
@pytest.mark.acceptance
def test_registry_builds_a_working_connector(base_config: DWSConfig):
    metadata = connector_registry.get_metadata("dws")
    connector_class = metadata.connector_class
    assert connector_class is DWSConnector
    assert metadata.config_class is DWSConfig

    connector = connector_class(base_config)
    try:
        assert connector.test_connection()
        assert connector.dialect == "dws"
    finally:
        connector.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_identifier_round_trips_through_the_parser(dws_objects: DWSConnector):
    identifier = dws_objects.identifier(
        database_name=dws_objects.database_name,
        schema_name=dws_objects.schema_name,
        table_name="t_row_hash",
    )
    parsed = parse_dws_identifier(identifier)

    assert parsed["database_name"] == dws_objects.database_name
    assert parsed["schema_name"] == dws_objects.schema_name
    assert parsed["table_name"] == "t_row_hash"
    assert parsed["catalog_name"] == ""


@pytest.mark.integration
def test_uri_and_context_round_trip(base_config: DWSConfig):
    uri = build_dws_uri(base_config)
    dialect, catalog, database, schema = resolve_dws_context(base_config, uri)

    assert dialect == "dws"
    assert catalog == ""
    assert database == base_config.database
    assert schema == base_config.schema_name
    assert base_config.password not in uri


@pytest.mark.integration
@pytest.mark.acceptance
def test_compatibility_mode_is_probed_from_the_catalog(connector: DWSConnector):
    mode = connector.get_compatibility_mode()

    # DWS reports ORA, TD or MySQL — never GaussDB's A/B/PG naming.
    assert mode in ("ORA", "TD", "MYSQL"), f"unexpected compatibility mode: {mode}"


@pytest.mark.integration
def test_server_version_is_reported_without_parsing_the_banner(connector: DWSConnector):
    version, banner = connector.get_server_version()

    assert version.startswith("9.")
    assert "GaussDB" in banner or "PostgreSQL" in banner


@pytest.mark.integration
@pytest.mark.acceptance
def test_migration_capabilities_carry_dws_specific_warnings(connector: DWSConnector):
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["dialect_family"] == "dws"
    notes = " ".join(capabilities["notes"])
    assert "TO GROUP" in notes and "TABLESPACE" in notes

    if connector.get_compatibility_mode() == "ORA":
        # Empty strings become NULL on write, so data cannot round-trip.
        assert "empty strings are stored" in notes


@pytest.mark.integration
def test_ora_mode_empty_string_is_null(connector: DWSConnector):
    """Pin the one ORA-mode difference no SQL rewrite can undo."""
    if connector.get_compatibility_mode() != "ORA":
        pytest.skip("empty-string folding is ORA-mode behaviour")

    result = connector.execute(
        {"sql_query": "SELECT CAST(('' IS NULL) AS INTEGER) AS empty_is_null"},
        result_format="list",
    )
    assert result.success, result.error
    assert result.sql_return == [{"empty_is_null": 1}]
