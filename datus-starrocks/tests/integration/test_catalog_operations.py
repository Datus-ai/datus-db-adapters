# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_starrocks import StarRocksConfig, StarRocksConnector

# ==================== Catalog Tests (CatalogSupportMixin) ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_catalogs(connector: StarRocksConnector):
    """Test getting list of catalogs."""
    catalogs = connector.get_catalogs()
    assert len(catalogs) > 0
    assert connector.default_catalog() in catalogs


@pytest.mark.integration
@pytest.mark.acceptance
def test_default_catalog(connector: StarRocksConnector):
    """Test default catalog value."""
    assert connector.default_catalog() == "default_catalog"


@pytest.mark.integration
@pytest.mark.acceptance
def test_switch_catalog(connector: StarRocksConnector, hive_catalog_setup: str):
    """Test switching catalogs."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup
    finally:
        connector.switch_catalog(original_catalog)
    assert connector.catalog_name == original_catalog


@pytest.mark.integration
def test_get_databases_from_default_catalog(connector: StarRocksConnector, config: StarRocksConfig):
    """The default catalog exposes the test database."""
    connector.switch_catalog(connector.default_catalog())

    databases = connector.get_databases()
    assert config.database in databases


@pytest.mark.integration
def test_get_databases_from_custom_catalog(connector: StarRocksConnector, hive_catalog_setup: str):
    """A Hive catalog exposes the metastore's own databases, not StarRocks'."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)
        databases = connector.get_databases(catalog_name=hive_catalog_setup)
        assert "default" in databases
    finally:
        connector.switch_catalog(original_catalog)


@pytest.mark.integration
def test_get_databases_exclude_system(connector: StarRocksConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)

    # System databases should be filtered out
    system_dbs = ["information_schema", "_statistics_"]
    for sys_db in system_dbs:
        assert sys_db not in databases


@pytest.mark.integration
def test_catalog_context_persists(connector: StarRocksConnector, hive_catalog_setup: str):
    """A listing issued after SET CATALOG stays in the switched catalog."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup

        # Unscoped, so this resolves through the switched catalog.
        assert "default" in connector.get_databases()

        assert connector.catalog_name == hive_catalog_setup
    finally:
        connector.switch_catalog(original_catalog)


@pytest.mark.integration
def test_switch_back_to_original_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    hive_catalog_setup: str,
):
    """Switching away and back restores the original catalog's databases."""
    original_catalog = connector.catalog_name

    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup
        assert "default" in connector.get_databases()

        connector.switch_catalog(original_catalog)
        assert connector.catalog_name == original_catalog
        assert config.database in connector.get_databases()
    finally:
        connector.switch_catalog(original_catalog)


# ==================== Cross-catalog scoping ====================

METADATA_TABLE = "datus_metadata_table"


@pytest.mark.integration
def test_cross_catalog_listing_from_default_session_targets_hive(
    connector: StarRocksConnector, hive_catalog_setup: str
):
    """An explicit Hive catalog argument wins over the default-catalog session.

    The metastore's ``default`` database is empty, so any internal table leaking
    into this listing would betray the request being resolved in the session
    catalog instead of the requested one.
    """
    tables = connector.get_tables(catalog_name=hive_catalog_setup, database_name="default")
    assert tables == []


@pytest.mark.integration
def test_cross_catalog_metadata_resolves_in_the_requested_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    hive_catalog_setup: str,
    metadata_objects_setup,
):
    """Explicit internal-catalog arguments win over a Hive-catalog session.

    End-to-end regression coverage for ``get_sample_rows()`` dropping the
    catalog: with the session switched to the Hive catalog, a no-``tables``
    sampling that explicitly requests the internal catalog must list and sample
    the internal objects. Before the fix the listing resolved in the session
    catalog, so it came back empty or with another catalog's tables.
    """
    internal = connector.default_catalog()
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)

        tables = connector.get_tables(catalog_name=internal, database_name=config.database)
        assert METADATA_TABLE in tables

        columns = connector.get_schema(catalog_name=internal, database_name=config.database, table_name=METADATA_TABLE)
        assert [column["name"] for column in columns] == ["id", "value"]

        samples = connector.get_sample_rows(catalog_name=internal, database_name=config.database)
        matched = [row for row in samples if row["table_name"] == METADATA_TABLE]
        assert len(matched) == 1, f"{METADATA_TABLE} missing from {[row['table_name'] for row in samples]}"
        assert matched[0]["catalog_name"] == internal
        assert "1,10" in matched[0]["sample_rows"]
    finally:
        connector.switch_catalog(original_catalog)
        connector.switch_context(database_name=config.database)
