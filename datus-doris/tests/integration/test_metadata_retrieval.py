# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector

# ==================== Table Metadata Tests ====================

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: DorisConnector, config: DorisConfig):
    """Test getting table list."""
    tables = connector.get_tables()
    assert f"{config.database}.{METADATA_TABLE}" in tables


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_with_ddl(connector: DorisConnector, config: DorisConfig):
    """Test getting tables with DDL definitions."""
    tables = connector.get_tables_with_ddl(catalog_name=config.catalog)

    table = next(item for item in tables if item["table_name"] == METADATA_TABLE)
    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["database_name"] == config.database
    assert table["schema_name"] == ""
    assert table["catalog_name"] == config.catalog
    assert table["identifier"] == f"{config.catalog}.{config.database}.{METADATA_TABLE}"


@pytest.mark.integration
def test_get_tables_with_catalog_filter(connector: DorisConnector, config: DorisConfig):
    """Test getting tables with catalog filter."""
    tables = connector.get_tables(catalog_name=config.catalog, database_name=config.database)
    assert isinstance(tables, list)

    assert METADATA_TABLE in tables


@pytest.mark.integration
def test_get_tables_metadata_includes_catalog(connector: DorisConnector, config: DorisConfig):
    """Test that table metadata includes catalog_name."""
    tables = connector.get_tables_with_ddl(catalog_name=config.catalog, database_name=config.database)

    for table in tables:
        assert "catalog_name" in table
        assert table["catalog_name"] == config.catalog
    assert METADATA_TABLE in {table["table_name"] for table in tables}


# ==================== View Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_views(connector: DorisConnector, config: DorisConfig):
    """Test getting view list."""
    views = connector.get_views()
    assert f"{config.database}.{METADATA_VIEW}" in views


@pytest.mark.integration
def test_get_views_with_ddl(connector: DorisConnector, config: DorisConfig):
    """Test getting views with DDL definitions."""
    views = connector.get_views_with_ddl(catalog_name=config.catalog)

    view = next(item for item in views if item["table_name"] == METADATA_VIEW)
    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["database_name"] == config.database
    assert view["schema_name"] == ""
    assert view["catalog_name"] == config.catalog


@pytest.mark.integration
def test_get_views_identifier_format(connector: DorisConnector, config: DorisConfig):
    """Test view identifier includes catalog."""
    views = connector.get_views_with_ddl(catalog_name=config.catalog, database_name=config.database)

    view = next(item for item in views if item["table_name"] == METADATA_VIEW)
    assert view["identifier"] == f"{config.catalog}.{config.database}.{METADATA_VIEW}"


# ==================== Sample Data Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_sample_rows_default(connector: DorisConnector):
    """Test getting sample rows with defaults."""
    sample_rows = connector.get_sample_rows()
    assert METADATA_TABLE in {item["table_name"] for item in sample_rows}


@pytest.mark.integration
def test_get_sample_rows_with_catalog(connector: DorisConnector, config: DorisConfig):
    """Test getting sample rows for specific catalog and database."""
    sample_rows = connector.get_sample_rows(catalog_name=config.catalog, database_name=config.database)

    item = next(row for row in sample_rows if row["table_name"] == METADATA_TABLE)
    assert item["database_name"] == config.database
    assert item["catalog_name"] == config.catalog
    assert item["schema_name"] == ""
    assert item["identifier"] == f"{config.catalog}.{config.database}.{METADATA_TABLE}"
    assert "1,10" in item["sample_rows"]


@pytest.mark.integration
def test_get_sample_rows_specific_tables(connector: DorisConnector, config: DorisConfig):
    """Test getting sample rows for specific tables."""
    sample_rows = connector.get_sample_rows(
        catalog_name=config.catalog,
        database_name=config.database,
        tables=[METADATA_TABLE],
        top_n=3,
    )

    assert len(sample_rows) == 1
    assert sample_rows[0]["table_name"] == METADATA_TABLE
    assert sample_rows[0]["catalog_name"] == config.catalog
