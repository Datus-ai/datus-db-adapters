# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConnector

# ==================== Catalog Tests (CatalogSupportMixin) ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_catalogs(connector: DorisConnector):
    """Test getting list of catalogs."""
    catalogs = connector.get_catalogs()
    assert len(catalogs) > 0
    assert connector.default_catalog() in catalogs


@pytest.mark.integration
@pytest.mark.acceptance
def test_default_catalog(connector: DorisConnector):
    """Test default catalog value."""
    assert connector.default_catalog() == "internal"


@pytest.mark.integration
@pytest.mark.acceptance
def test_switch_catalog(connector: DorisConnector, hive_catalog_setup: str):
    """Test switching catalogs."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup
    finally:
        connector.switch_catalog(original_catalog)
    assert connector.catalog_name == original_catalog


@pytest.mark.integration
def test_get_databases_from_default_catalog(connector: DorisConnector):
    """Test getting databases from default catalog."""
    # Ensure we're in default catalog
    connector.switch_catalog(connector.default_catalog())

    databases = connector.get_databases()
    assert isinstance(databases, list)
    assert len(databases) > 0


@pytest.mark.integration
def test_get_databases_from_custom_catalog(connector: DorisConnector, hive_catalog_setup: str):
    """Test getting databases from Hive catalog."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)
        databases = connector.get_databases(catalog_name=hive_catalog_setup)
        assert "default" in databases
    finally:
        connector.switch_catalog(original_catalog)


@pytest.mark.integration
def test_get_databases_exclude_system(connector: DorisConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)

    # System databases should be filtered out
    system_dbs = ["information_schema", "mysql", "__internal_schema"]
    for sys_db in system_dbs:
        assert sys_db not in databases


@pytest.mark.integration
def test_catalog_context_persists(connector: DorisConnector, hive_catalog_setup: str):
    """Test that catalog context persists across operations."""
    original_catalog = connector.catalog_name
    try:
        connector.switch_catalog(hive_catalog_setup)

        # Catalog should persist
        assert connector.catalog_name == hive_catalog_setup

        # Get databases (should use the switched catalog)
        databases = connector.get_databases()
        assert isinstance(databases, list)

        # Catalog should still be the same
        assert connector.catalog_name == hive_catalog_setup
    finally:
        connector.switch_catalog(original_catalog)


@pytest.mark.integration
def test_switch_back_to_original_catalog(connector: DorisConnector, hive_catalog_setup: str):
    """Test switching back to original catalog."""
    original_catalog = connector.catalog_name

    try:
        # Switch to Hive catalog
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup

        # Switch back to original
        connector.switch_catalog(original_catalog)
        assert connector.catalog_name == original_catalog

        # Verify we can still access databases
        databases = connector.get_databases()
        assert isinstance(databases, list)
    finally:
        connector.switch_catalog(original_catalog)
