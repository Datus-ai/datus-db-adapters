# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_starrocks import StarRocksConfig, StarRocksConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """The fixture table is listed both unscoped and scoped."""
    assert f"{config.database}.{METADATA_TABLE}" in connector.get_tables()
    assert METADATA_TABLE in connector.get_tables(
        catalog_name=config.catalog,
        database_name=config.database,
    )


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_with_ddl(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Every coordinate of a table entry, compared against the created table."""
    table = next(
        item
        for item in connector.get_tables_with_ddl(
            catalog_name=config.catalog,
            database_name=config.database,
        )
        if item["table_name"] == METADATA_TABLE
    )

    assert "CREATE TABLE" in table["definition"].upper()
    assert table["table_type"] == "table"
    assert table["catalog_name"] == config.catalog
    assert table["database_name"] == config.database
    assert table["schema_name"] == ""
    assert table["identifier"] == f"{config.catalog}.{config.database}.{METADATA_TABLE}"


@pytest.mark.integration
def test_get_tables_qualification_follows_the_requested_scope(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """A listing names objects only by the levels the caller left unspecified.

    An unscoped listing has to stay addressable, so it prefixes the database; a
    listing already scoped to one database must not repeat it.
    """
    unscoped = connector.get_tables()
    scoped = connector.get_tables(catalog_name=config.catalog, database_name=config.database)

    assert f"{config.database}.{METADATA_TABLE}" in unscoped
    assert METADATA_TABLE not in unscoped
    assert METADATA_TABLE in scoped
    assert f"{config.database}.{METADATA_TABLE}" not in scoped

    assert connector.get_tables(catalog_name=config.catalog, database_name="datus_absent_database") == []


@pytest.mark.integration
def test_get_tables_metadata_includes_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Every row of a catalog-scoped listing carries that catalog."""
    tables = connector.get_tables_with_ddl(catalog_name=config.catalog, database_name=config.database)

    assert METADATA_TABLE in {table["table_name"] for table in tables}
    for table in tables:
        assert table["catalog_name"] == config.catalog
        assert table["database_name"] == config.database


@pytest.mark.integration
def test_get_schema_reports_column_types_and_nullability(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Column metadata matches the DDL the fixture issued."""
    columns = connector.get_schema(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=METADATA_TABLE,
    )

    assert [column["name"] for column in columns] == ["id", "value"]
    assert [column["cid"] for column in columns] == [0, 1]

    by_name = {column["name"]: column for column in columns}
    assert by_name["id"]["type"].upper().startswith("BIGINT")
    assert by_name["id"]["nullable"] is False
    assert by_name["value"]["type"].upper().startswith("INT")
    assert by_name["value"]["nullable"] is True


# ==================== View Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_views(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """The fixture view is listed, and is not reported as a table."""
    assert f"{config.database}.{METADATA_VIEW}" in connector.get_views()
    assert METADATA_VIEW in connector.get_views(
        catalog_name=config.catalog,
        database_name=config.database,
    )
    assert METADATA_VIEW not in connector.get_tables(
        catalog_name=config.catalog,
        database_name=config.database,
    )


@pytest.mark.integration
def test_get_views_with_ddl(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Every coordinate of a view entry, compared against the created view."""
    view = next(
        item
        for item in connector.get_views_with_ddl(
            catalog_name=config.catalog,
            database_name=config.database,
        )
        if item["table_name"] == METADATA_VIEW
    )

    assert "CREATE VIEW" in view["definition"].upper()
    assert view["table_type"] == "view"
    assert view["catalog_name"] == config.catalog
    assert view["database_name"] == config.database
    assert view["schema_name"] == ""
    assert view["identifier"] == f"{config.catalog}.{config.database}.{METADATA_VIEW}"


@pytest.mark.integration
def test_get_views_identifier_format(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """A view identifier is catalog.database.view — three parts, in that order."""
    views = connector.get_views_with_ddl(catalog_name=config.catalog, database_name=config.database)

    assert METADATA_VIEW in {view["table_name"] for view in views}
    for view in views:
        assert view["identifier"].split(".") == [
            view["catalog_name"],
            view["database_name"],
            view["table_name"],
        ]


# ==================== Sample Data Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_sample_rows_default(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """An unscoped sample walks the connector's own database."""
    sample_rows = connector.get_sample_rows()

    entry = next(item for item in sample_rows if item["table_name"] == METADATA_TABLE)
    assert entry["catalog_name"] == config.catalog
    assert entry["database_name"] == config.database
    assert entry["schema_name"] == ""
    assert entry["identifier"] == f"{config.catalog}.{config.database}.{METADATA_TABLE}"
    assert "1,10" in entry["sample_rows"]
    assert "2,20" in entry["sample_rows"]


@pytest.mark.integration
def test_get_sample_rows_with_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Sampling a catalog and database keeps both on every entry."""
    sample_rows = connector.get_sample_rows(catalog_name=config.catalog, database_name=config.database)

    assert METADATA_TABLE in {item["table_name"] for item in sample_rows}
    for item in sample_rows:
        assert item["catalog_name"] == config.catalog
        assert item["database_name"] == config.database
        assert item["schema_name"] == ""
        assert item["identifier"].split(".") == [
            item["catalog_name"],
            item["database_name"],
            item["table_name"],
        ]


@pytest.mark.integration
def test_get_sample_rows_specific_tables(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Naming one table samples that table and nothing else."""
    sample_rows = connector.get_sample_rows(
        catalog_name=config.catalog,
        database_name=config.database,
        tables=[METADATA_TABLE],
        top_n=3,
    )

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": f"{config.catalog}.{config.database}.{METADATA_TABLE}",
        "catalog_name": config.catalog,
        "database_name": config.database,
        "schema_name": "",
        "table_name": METADATA_TABLE,
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert sample_rows[0]["sample_rows"].splitlines()[0] == "id,value"
    assert "1,10" in sample_rows[0]["sample_rows"]
