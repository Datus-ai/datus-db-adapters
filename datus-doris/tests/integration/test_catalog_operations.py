# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_catalog_discovery(connector: DorisConnector):
    catalogs = connector.get_catalogs()
    assert connector.default_catalog() == "internal"
    assert "internal" in catalogs


@pytest.mark.integration
def test_default_catalog_databases(
    connector: DorisConnector,
    config: DorisConfig,
):
    databases = connector.get_databases(include_sys=False)

    assert config.database in databases
    assert {
        "information_schema",
        "mysql",
        "__internal_schema",
    }.isdisjoint(databases)


@pytest.mark.integration
def test_query_external_catalog_without_changing_context(
    connector: DorisConnector,
    hive_catalog_setup: str,
):
    original_context = connector.get_current_context()

    assert "default" in connector.get_databases(
        catalog_name=hive_catalog_setup,
        include_sys=True,
    )
    assert connector.get_current_context() == original_context


@pytest.mark.integration
@pytest.mark.acceptance
def test_switch_external_catalog_and_restore(
    connector: DorisConnector,
    hive_catalog_setup: str,
):
    original_context = connector.get_current_context()
    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup
        assert connector.database_name == ""
        assert "default" in connector.get_databases(include_sys=True)
    finally:
        connector.switch_context(
            catalog_name=original_context["catalog_name"],
            database_name=original_context["database_name"],
        )

    assert connector.get_current_context() == original_context


METADATA_TABLE = "datus_metadata_table"


@pytest.mark.integration
def test_cross_catalog_sampling_resolves_in_the_requested_catalog(
    connector: DorisConnector,
    config: DorisConfig,
    hive_catalog_setup: str,
    metadata_objects_setup,
):
    """Explicit internal-catalog sampling must survive a Hive-catalog session.

    Doris overrides get_sample_rows precisely so the requested catalog is not
    lost (the MySQL base method used to drop it). Pin that behavior from the
    session's point of view: with the session switched to the Hive catalog, a
    no-``tables`` sampling that explicitly requests the internal catalog must
    still list and sample the provisioned internal objects — so a future
    "simplification" back to the base implementation cannot regress silently.
    """
    original_context = connector.get_current_context()
    try:
        connector.switch_catalog(hive_catalog_setup)

        tables = connector.get_tables(catalog_name="internal", database_name=config.database)
        assert METADATA_TABLE in tables

        samples = connector.get_sample_rows(catalog_name="internal", database_name=config.database)
        matched = [row for row in samples if row["table_name"] == METADATA_TABLE]
        assert len(matched) == 1, f"{METADATA_TABLE} missing from {[row['table_name'] for row in samples]}"
        assert matched[0]["catalog_name"] == "internal"
        assert "1," in matched[0]["sample_rows"]
    finally:
        connector.switch_context(
            catalog_name=original_context["catalog_name"],
            database_name=original_context["database_name"],
        )
