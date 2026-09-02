# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_trino import TrinoConfig, TrinoConnector

from .conftest import WRITABLE_CATALOG, WRITABLE_SCHEMA

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# ==================== Catalog Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_catalogs(connector: TrinoConnector, config: TrinoConfig):
    """The configured catalog and the built-in system catalog are both listed."""
    catalogs = connector.get_catalogs()
    assert config.catalog in catalogs
    assert "system" in catalogs


@pytest.mark.integration
@pytest.mark.acceptance
def test_default_catalog(connector: TrinoConnector, config: TrinoConfig):
    """The default catalog is exactly the one the connector was configured with."""
    assert connector.default_catalog() == config.catalog


@pytest.mark.integration
def test_switch_catalog(connector: TrinoConnector):
    """Test switching catalogs."""
    original_catalog = connector.catalog_name
    catalogs = connector.get_catalogs()

    if len(catalogs) > 1:
        target_catalog = [c for c in catalogs if c != original_catalog][0]
        connector.switch_catalog(target_catalog)
        assert connector.catalog_name == target_catalog

        connector.switch_catalog(original_catalog)
        assert connector.catalog_name == original_catalog
    else:
        pytest.skip("Only one catalog available")


# ==================== Schema/Database Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schemas(connector: TrinoConnector, config: TrinoConfig, metadata_objects_setup):
    """The schema holding the fixture objects is listed."""
    schemas = connector.get_schemas(catalog_name=config.catalog)
    assert config.schema_name in schemas
    # The fixture objects live in the writable catalog, which in CI is a
    # different one; listing it must show their schema too.
    assert WRITABLE_SCHEMA in connector.get_schemas(catalog_name=WRITABLE_CATALOG)


@pytest.mark.integration
def test_get_databases(connector: TrinoConnector, config: TrinoConfig, metadata_objects_setup):
    """Trino has no database level: databases are exactly the schemas."""
    databases = connector.get_databases(catalog_name=config.catalog)
    assert config.schema_name in databases
    assert WRITABLE_SCHEMA in connector.get_databases(catalog_name=WRITABLE_CATALOG)
    assert databases == connector.get_schemas(catalog_name=config.catalog)


@pytest.mark.integration
def test_get_schemas_exclude_system(connector: TrinoConnector, config: TrinoConfig):
    """Test that system schemas are excluded by default."""
    schemas = connector.get_schemas(catalog_name=config.catalog, include_sys=False)
    assert "information_schema" not in schemas


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: TrinoConnector, config: TrinoConfig, metadata_objects_setup):
    """The fixture table is listed bare when scoped and fully qualified when not."""
    tables = connector.get_tables(catalog_name=WRITABLE_CATALOG, schema_name=WRITABLE_SCHEMA)
    assert METADATA_TABLE in tables
    # An unscoped listing follows the session catalog/schema and qualifies every
    # name it returns. Asserted on shape rather than on a specific table: the
    # session catalog is tpch in CI and memory locally, and the fixture objects
    # live in the writable one either way.
    unscoped = connector.get_tables()
    assert unscoped, "the session catalog/schema should list at least one table"
    assert all(name.count(".") == 2 for name in unscoped), unscoped[:3]

    tables = [
        item
        for item in connector.get_tables_with_ddl(
            catalog_name=WRITABLE_CATALOG,
            schema_name=WRITABLE_SCHEMA,
        )
        if item["table_name"] == METADATA_TABLE
    ]
    assert len(tables) == 1, f"expected exactly one entry, got {tables}"
    table = tables[0]
    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["catalog_name"] == WRITABLE_CATALOG
    assert table["database_name"] == WRITABLE_SCHEMA
    assert table["schema_name"] == WRITABLE_SCHEMA
    assert table["identifier"] == f'"{WRITABLE_CATALOG}"."{WRITABLE_SCHEMA}"."{METADATA_TABLE}"'


@pytest.mark.integration
def test_get_views(connector: TrinoConnector, config: TrinoConfig, metadata_objects_setup):
    """The fixture view is listed, with every coordinate of its DDL entry exact."""
    views = connector.get_views(catalog_name=WRITABLE_CATALOG, schema_name=WRITABLE_SCHEMA)
    assert METADATA_VIEW in views

    views = [
        item
        for item in connector.get_views_with_ddl(
            catalog_name=WRITABLE_CATALOG,
            schema_name=WRITABLE_SCHEMA,
        )
        if item["table_name"] == METADATA_VIEW
    ]
    assert len(views) == 1, f"expected exactly one entry, got {views}"
    view = views[0]
    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == WRITABLE_CATALOG
    assert view["database_name"] == WRITABLE_SCHEMA
    assert view["schema_name"] == WRITABLE_SCHEMA
    assert view["identifier"] == f'"{WRITABLE_CATALOG}"."{WRITABLE_SCHEMA}"."{METADATA_VIEW}"'


# ==================== Sample Data Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_sample_rows(connector: TrinoConnector, metadata_objects_setup):
    """Naming one table samples that table and nothing else, rows included."""
    sample_rows = connector.get_sample_rows(
        tables=[METADATA_TABLE], top_n=3, catalog_name=WRITABLE_CATALOG, schema_name=WRITABLE_SCHEMA
    )

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": f"{WRITABLE_CATALOG}.{WRITABLE_SCHEMA}.{METADATA_TABLE}",
        "catalog_name": WRITABLE_CATALOG,
        # Sampling leaves database_name empty: Trino addresses objects as
        # catalog.schema, so the middle level the other engines call a database
        # has no value to report here.
        "database_name": "",
        "schema_name": WRITABLE_SCHEMA,
        "table_name": METADATA_TABLE,
        "table_type": "table",
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert sample_rows[0]["sample_rows"].splitlines()[0] == "id,value"
    assert "1,10" in sample_rows[0]["sample_rows"]
    assert "2,20" in sample_rows[0]["sample_rows"]
