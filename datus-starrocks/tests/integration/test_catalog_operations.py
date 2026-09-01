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
