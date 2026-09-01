# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_greenplum import GreenplumConfig, GreenplumConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# ==================== Database Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: GreenplumConnector, config: GreenplumConfig):
    """The database the tests connect to is listed."""
    assert config.database in connector.get_databases()


@pytest.mark.integration
def test_get_databases_exclude_system(connector: GreenplumConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"template0", "template1", "gpperfmon"}
    for db in databases:
        assert db not in system_dbs


# ==================== Schema Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schemas(connector: GreenplumConnector, config: GreenplumConfig):
    """The schema the fixture objects live in is listed."""
    schemas = connector.get_schemas()
    assert config.schema_name in schemas
    assert "public" in schemas


@pytest.mark.integration
def test_get_schemas_exclude_system(connector: GreenplumConnector):
    """Test that system schemas are excluded by default."""
    schemas = connector.get_schemas(include_sys=False)
    system_schemas = {"pg_catalog", "information_schema", "pg_toast", "gp_toolkit", "pg_aoseg", "pg_bitmapindex"}
    for schema in schemas:
        assert schema not in system_schemas


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: GreenplumConnector, config: GreenplumConfig, metadata_objects_setup):
    """A listing names objects only by the levels the caller left unspecified."""
    assert METADATA_TABLE in connector.get_tables(
        database_name=config.database,
        schema_name=config.schema_name,
    )
    assert f"{config.database}.{METADATA_TABLE}" in connector.get_tables(schema_name=config.schema_name)
    assert f"{config.database}.{config.schema_name}.{METADATA_TABLE}" in connector.get_tables()


@pytest.mark.integration
def test_get_tables_with_ddl_of_fixture_table(
    connector: GreenplumConnector,
    config: GreenplumConfig,
    metadata_objects_setup,
):
    """Every coordinate of a table entry, compared against the created table."""
    table = next(
        item
        for item in connector.get_tables_with_ddl(schema_name=config.schema_name)
        if item["table_name"] == METADATA_TABLE
    )

    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["catalog_name"] == ""
    assert table["database_name"] == config.database
    assert table["schema_name"] == config.schema_name
    assert table["identifier"] == f"{config.database}.{config.schema_name}.{METADATA_TABLE}"


@pytest.mark.integration
def test_get_tables_with_ddl(connector: GreenplumConnector, config: GreenplumConfig):
    """A freshly created table is returned by a listing filtered to its own name."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"

    connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )

    try:
        tables = connector.get_tables_with_ddl(schema_name=config.schema_name, tables=[table_name])

        assert len(tables) == 1
        table = tables[0]
        assert table["table_name"] == table_name
        assert "CREATE TABLE" in table["definition"].upper()
        assert table["table_type"] == "table"
        assert table["schema_name"] == config.schema_name
        assert table["identifier"] == f"{config.database}.{config.schema_name}.{table_name}"
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== View Tests ====================


@pytest.mark.integration
def test_get_views(connector: GreenplumConnector, config: GreenplumConfig, metadata_objects_setup):
    """The fixture view is listed, and is not reported as a table."""
    assert METADATA_VIEW in connector.get_views(
        database_name=config.database,
        schema_name=config.schema_name,
    )
    assert f"{config.database}.{config.schema_name}.{METADATA_VIEW}" in connector.get_views()
    assert METADATA_VIEW not in connector.get_tables(
        database_name=config.database,
        schema_name=config.schema_name,
    )


@pytest.mark.integration
def test_get_views_with_ddl(connector: GreenplumConnector, config: GreenplumConfig, metadata_objects_setup):
    """Every coordinate of a view entry, compared against the created view."""
    view = next(
        item
        for item in connector.get_views_with_ddl(schema_name=config.schema_name)
        if item["table_name"] == METADATA_VIEW
    )

    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == ""
    assert view["database_name"] == config.database
    assert view["schema_name"] == config.schema_name
    assert view["identifier"] == f"{config.database}.{config.schema_name}.{METADATA_VIEW}"


# ==================== Column Schema Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema(connector: GreenplumConnector, config: GreenplumConfig):
    """Test getting table schema."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_schema_{suffix}"

    connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) NOT NULL,
            email VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    try:
        schema = connector.get_schema(schema_name=config.schema_name, table_name=table_name)

        assert len(schema) == 4

        # Check id column
        id_col = [col for col in schema if col["name"] == "id"][0]
        assert id_col["pk"] is True
        assert "int" in id_col["type"].lower()

        # Check name column
        name_col = [col for col in schema if col["name"] == "name"][0]
        assert name_col["nullable"] is False
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Sample Data Tests ====================


@pytest.mark.integration
def test_get_sample_rows(connector: GreenplumConnector, config: GreenplumConfig, metadata_objects_setup):
    """Naming one table samples that table and nothing else, rows included."""
    sample_rows = connector.get_sample_rows(schema_name=config.schema_name, tables=[METADATA_TABLE], top_n=3)

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": f"{config.database}.{config.schema_name}.{METADATA_TABLE}",
        "catalog_name": "",
        "database_name": config.database,
        "schema_name": config.schema_name,
        "table_name": METADATA_TABLE,
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert sample_rows[0]["sample_rows"].splitlines()[0] == "id,value"
    assert "1,10" in sample_rows[0]["sample_rows"]
    assert "2,20" in sample_rows[0]["sample_rows"]
