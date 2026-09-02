# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_spark import SparkConfig, SparkConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# ==================== Database Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: SparkConnector, config: SparkConfig, metadata_objects_setup):
    """The database holding the fixture objects is listed."""
    assert (config.database or "default") in connector.get_databases()


@pytest.mark.integration
def test_get_databases_exclude_system(connector: SparkConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    assert "information_schema" not in databases


@pytest.mark.integration
def test_get_schemas_returns_empty(connector: SparkConnector):
    """Test that get_schemas returns empty list."""
    schemas = connector.get_schemas()
    assert schemas == []


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: SparkConnector, config: SparkConfig, metadata_objects_setup):
    """The fixture table is listed bare when scoped and database-qualified when not."""
    db = config.database or "default"
    assert METADATA_TABLE in connector.get_tables(database_name=db)
    assert f"{db}.{METADATA_TABLE}" in connector.get_tables()

    tables = [item for item in connector.get_tables_with_ddl(database_name=db) if item["table_name"] == METADATA_TABLE]
    assert len(tables) == 1, f"expected exactly one entry, got {tables}"
    table = tables[0]
    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["catalog_name"] == ""
    assert table["database_name"] == db
    assert table["schema_name"] == db
    assert table["identifier"] == f"`{db}`.`{METADATA_TABLE}`"


@pytest.mark.integration
def test_get_views(connector: SparkConnector, config: SparkConfig, metadata_objects_setup):
    """The fixture view is listed, with every coordinate of its DDL entry exact."""
    db = config.database or "default"
    assert METADATA_VIEW in connector.get_views(database_name=db)

    view = next(item for item in connector.get_views_with_ddl(database_name=db) if item["table_name"] == METADATA_VIEW)
    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == ""
    assert view["database_name"] == db
    assert view["schema_name"] == db
    assert view["identifier"] == f"`{db}`.`{METADATA_VIEW}`"


@pytest.mark.integration
def test_get_schema(connector: SparkConnector, config: SparkConfig, metadata_objects_setup):
    """Column metadata matches the DDL the fixture issued."""
    db = config.database or "default"
    columns = connector.get_schema(database_name=db, table_name=METADATA_TABLE)

    assert [column["name"] for column in columns] == ["id", "value"]
    assert [column["cid"] for column in columns] == [0, 1]

    by_name = {column["name"]: column for column in columns}
    assert by_name["id"]["type"] == "bigint"
    assert by_name["value"]["type"] == "int"


# ==================== Sample Data Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_sample_rows(connector: SparkConnector, config: SparkConfig, metadata_objects_setup):
    """Naming one table samples that table and nothing else, rows included."""
    db = config.database or "default"
    sample_rows = connector.get_sample_rows(database_name=db, tables=[METADATA_TABLE], top_n=3)

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": f"{db}.{METADATA_TABLE}",
        "catalog_name": "",
        "database_name": db,
        "schema_name": "",
        "table_name": METADATA_TABLE,
        "table_type": "table",
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert "1,10" in sample_rows[0]["sample_rows"]
    assert "2,20" in sample_rows[0]["sample_rows"]
