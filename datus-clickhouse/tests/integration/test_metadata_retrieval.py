# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# ==================== Database Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: ClickHouseConnector, config: ClickHouseConfig, metadata_objects_setup):
    """The database holding the fixture objects is listed."""
    assert config.database in connector.get_databases()


@pytest.mark.integration
def test_get_databases_exclude_system(connector: ClickHouseConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"INFORMATION_SCHEMA", "information_schema", "system"}
    for db in databases:
        assert db not in system_dbs


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: ClickHouseConnector, config: ClickHouseConfig, metadata_objects_setup):
    """The fixture table is listed bare when scoped and database-qualified when not."""
    assert METADATA_TABLE in connector.get_tables(database_name=config.database)
    assert f"{config.database}.{METADATA_TABLE}" in connector.get_tables()


@pytest.mark.integration
def test_get_tables_with_ddl_of_fixture_table(
    connector: ClickHouseConnector,
    config: ClickHouseConfig,
    metadata_objects_setup,
):
    """Every coordinate of a table entry, compared against the created table."""
    table = next(
        item
        for item in connector.get_tables_with_ddl(database_name=config.database)
        if item["table_name"] == METADATA_TABLE
    )

    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["catalog_name"] == ""
    assert table["database_name"] == config.database
    assert table["schema_name"] == ""
    assert table["identifier"] == f"{config.database}.{METADATA_TABLE}"


@pytest.mark.integration
def test_get_tables_with_ddl(connector: ClickHouseConnector, config: ClickHouseConfig):
    """A freshly created table is returned by a listing filtered to its own name."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            `id` Int64,
            `name` Nullable(String)
            ) ENGINE = MergeTree()
            ORDER BY id
    """
    )

    try:
        tables = connector.get_tables_with_ddl(database_name=config.database, tables=[table_name])

        assert len(tables) == 1
        table = tables[0]
        assert table["table_name"] == table_name
        assert "CREATE TABLE" in table["definition"].upper()
        assert table["table_type"] == "table"
        assert table["database_name"] == config.database
        assert table["schema_name"] == ""
        assert table["identifier"] == f"{config.database}.{table_name}"
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== View Tests ====================


@pytest.mark.integration
def test_get_views(connector: ClickHouseConnector, config: ClickHouseConfig, metadata_objects_setup):
    """The fixture view is listed as a view, and is not reported as a table.

    View names come back bare even from an unscoped call: ``get_views`` is the
    inherited SQLAlchemy listing, which resolves the connector's own database
    into the inspector's schema argument and therefore never prefixes it.
    """
    assert METADATA_VIEW in connector.get_views(database_name=config.database)
    assert METADATA_VIEW in connector.get_views()
    assert METADATA_VIEW not in connector.get_tables(database_name=config.database)


@pytest.mark.integration
def test_get_views_with_ddl(connector: ClickHouseConnector, config: ClickHouseConfig, metadata_objects_setup):
    """Every coordinate of a view entry, compared against the created view."""
    view = next(
        item
        for item in connector.get_views_with_ddl(database_name=config.database)
        if item["table_name"] == METADATA_VIEW
    )

    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == ""
    assert view["database_name"] == config.database
    assert view["schema_name"] == ""
    assert view["identifier"] == f"{config.database}.{METADATA_VIEW}"


# ==================== Schema Tests ====================


@pytest.mark.integration
def test_get_schema_of_fixture_table(
    connector: ClickHouseConnector,
    config: ClickHouseConfig,
    metadata_objects_setup,
):
    """Column metadata matches the DDL the fixture issued."""
    columns = connector.get_schema(database_name=config.database, table_name=METADATA_TABLE)

    assert [column["name"] for column in columns] == ["id", "value"]
    assert [column["cid"] for column in columns] == [0, 1]

    by_name = {column["name"]: column for column in columns}
    assert by_name["id"]["type"] == "Int64"
    assert by_name["id"]["nullable"] is False
    assert by_name["value"]["type"] == "Nullable(Int32)"
    assert by_name["value"]["nullable"] is True


@pytest.mark.integration
def test_get_schema(connector: ClickHouseConnector, config: ClickHouseConfig):
    """Test getting table schema."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_schema_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            `id` String,
            `type` Nullable(String),
            `flag` Nullable(Int64),
            `entry_type` Nullable(String),
            `cnt` Nullable(Int64),
            `dt` String DEFAULT '1971-01-01'
        ) ENGINE = MergeTree()
        ORDER BY id
    """
    )

    try:
        schema = connector.get_schema(database_name=config.database, table_name=table_name)

        assert len(schema) == 6

        # Check flag column
        flag_col = next(col for col in schema if col["name"] == "flag")
        assert flag_col["pk"] is False
        assert "int64" in flag_col["type"].lower()

        # Check type column
        type_col = next(col for col in schema if col["name"] == "type")
        assert type_col["nullable"] is True
        assert "string" in type_col["type"].lower()
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Sample Data Tests ====================


@pytest.mark.integration
def test_get_sample_rows(connector: ClickHouseConnector, config: ClickHouseConfig, metadata_objects_setup):
    """Naming one table samples that table and nothing else, rows included."""
    sample_rows = connector.get_sample_rows(database_name=config.database, tables=[METADATA_TABLE], top_n=3)

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": f"{config.database}.{METADATA_TABLE}",
        "catalog_name": "",
        "database_name": config.database,
        "schema_name": "",
        "table_name": METADATA_TABLE,
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert sample_rows[0]["sample_rows"].splitlines()[0] == "id,value"
    assert "1,10" in sample_rows[0]["sample_rows"]
    assert "2,20" in sample_rows[0]["sample_rows"]
