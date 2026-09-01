# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_hive import HiveConfig, HiveConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """The database holding the fixture objects is listed."""
    assert (config.database or "default") in connector.get_databases()


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """The fixture table is listed bare when scoped and database-qualified when not."""
    database = config.database or "default"
    assert METADATA_TABLE in connector.get_tables(database_name=database)
    assert f"{database}.{METADATA_TABLE}" in connector.get_tables()


@pytest.mark.integration
def test_get_tables_with_ddl_of_fixture_table(
    connector: HiveConnector,
    config: HiveConfig,
    metadata_objects_setup,
):
    """Every coordinate of a table entry, compared against the created table.

    Hive 4 stores a plain ``CREATE TABLE`` as an external table, so its
    ``SHOW CREATE TABLE`` echoes ``CREATE EXTERNAL TABLE``.
    """
    database = config.database or "default"
    table = next(
        item for item in connector.get_tables_with_ddl(database_name=database) if item["table_name"] == METADATA_TABLE
    )

    definition = table["definition"].upper()
    assert definition.startswith("CREATE")
    assert "TABLE" in definition
    assert METADATA_TABLE.upper() in definition
    assert table["table_type"] == "table"
    assert table["catalog_name"] == ""
    assert table["database_name"] == database
    assert table["schema_name"] == ""
    assert table["identifier"] == f"{database}.{METADATA_TABLE}"


@pytest.mark.integration
def test_get_tables_with_ddl(connector: HiveConnector, config: HiveConfig):
    """A freshly created table is returned by a listing filtered to its own name."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"
    database = config.database or "default"

    connector.execute_ddl(f"CREATE DATABASE IF NOT EXISTS {database}")
    connector.switch_context(database_name=database)
    connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT,
            name STRING
        )
        """
    )

    try:
        tables = connector.get_tables_with_ddl(database_name=database, tables=[table_name])

        assert len(tables) == 1
        table = tables[0]
        assert table["table_name"] == table_name
        definition = table["definition"].upper()
        assert definition.startswith("CREATE")
        assert table_name.upper() in definition
        assert table["table_type"] == "table"
        assert table["database_name"] == database
        assert table["identifier"] == f"{database}.{table_name}"
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_get_views(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """The fixture view is listed by the view listing.

    Hive's ``SHOW TABLES`` reports views alongside tables, so the table listing
    carries the view too; only ``SHOW VIEWS`` distinguishes them.
    """
    database = config.database or "default"
    assert METADATA_VIEW in connector.get_views(database_name=database)
    assert f"{database}.{METADATA_VIEW}" in connector.get_views()


@pytest.mark.integration
def test_get_views_with_ddl(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """Every coordinate of a view entry, compared against the created view."""
    database = config.database or "default"
    view = next(
        item for item in connector.get_views_with_ddl(database_name=database) if item["table_name"] == METADATA_VIEW
    )

    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == ""
    assert view["database_name"] == database
    assert view["schema_name"] == ""
    assert view["identifier"] == f"{database}.{METADATA_VIEW}"


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """Column metadata matches the DDL the fixture issued."""
    database = config.database or "default"
    columns = connector.get_schema(database_name=database, table_name=METADATA_TABLE)

    assert [column["name"] for column in columns] == ["id", "value"]
    assert [column["cid"] for column in columns] == [0, 1]

    by_name = {column["name"]: column for column in columns}
    assert by_name["id"]["type"] == "int"
    assert by_name["value"]["type"] == "int"


@pytest.mark.integration
def test_get_sample_rows(connector: HiveConnector, config: HiveConfig, metadata_objects_setup):
    """Naming one table samples that table and nothing else, rows included."""
    database = config.database or "default"
    samples = connector.get_sample_rows(tables=[METADATA_TABLE], top_n=3, database_name=database)

    assert len(samples) == 1
    assert samples[0] == {
        "identifier": f"{database}.{METADATA_TABLE}",
        "catalog_name": "",
        "database_name": database,
        "schema_name": "",
        "table_name": METADATA_TABLE,
        "table_type": "table",
        "sample_rows": samples[0]["sample_rows"],
    }
    assert "1,10" in samples[0]["sample_rows"]
    assert "2,20" in samples[0]["sample_rows"]
