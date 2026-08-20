# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


@pytest.mark.integration
@pytest.mark.acceptance
def test_table_metadata(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    assert f"{config.database}.{METADATA_TABLE}" in connector.get_tables()
    assert METADATA_TABLE in connector.get_tables(
        catalog_name=config.catalog,
        database_name=config.database,
    )

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
    assert table["identifier"] == (f"{config.catalog}.{config.database}.{METADATA_TABLE}")


@pytest.mark.integration
def test_view_metadata(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    assert f"{config.database}.{METADATA_VIEW}" in connector.get_views()

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
    assert view["identifier"] == (f"{config.catalog}.{config.database}.{METADATA_VIEW}")


@pytest.mark.integration
@pytest.mark.acceptance
def test_schema_reports_duplicate_key_columns_as_non_unique(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    """A Duplicate Key column is a sort key, not a row identity.

    Doris fills ``information_schema.COLUMNS.COLUMN_KEY`` from the table model
    rather than MySQL's per-column key flag, so a key column of a DUPLICATE KEY
    table reports ``DUP`` and must not be surfaced as a primary key.
    """
    schema = connector.get_schema(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=METADATA_TABLE,
    )
    columns = {column["name"]: column for column in schema}

    assert set(columns) == {"id", "value"}
    assert columns["id"]["key_type"] == "DUP"
    assert columns["id"]["key"] is True
    assert columns["id"]["pk"] is False
    assert columns["id"]["nullable"] is False

    assert columns["value"]["key_type"] == ""
    assert columns["value"]["key"] is False
    assert columns["value"]["pk"] is False


@pytest.mark.integration
@pytest.mark.acceptance
def test_schema_reports_unique_key_columns_as_primary_key(
    connector: DorisConnector,
    config: DorisConfig,
    unique_key_table: str,
):
    """A Unique Key column identifies a row, so it is reported as a primary key."""
    schema = connector.get_schema(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=unique_key_table,
    )
    columns = {column["name"]: column for column in schema}

    assert columns["id"]["key_type"] == "UNI"
    assert columns["id"]["key"] is True
    assert columns["id"]["pk"] is True

    assert columns["name"]["key_type"] == ""
    assert columns["name"]["pk"] is False


@pytest.mark.integration
def test_sample_rows(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    sample_rows = connector.get_sample_rows(
        catalog_name=config.catalog,
        database_name=config.database,
        tables=[METADATA_TABLE],
        top_n=3,
    )

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "catalog_name": config.catalog,
        "database_name": config.database,
        "schema_name": "",
        "table_name": METADATA_TABLE,
        "table_type": "table",
        "identifier": f"{config.catalog}.{config.database}.{METADATA_TABLE}",
        "sample_rows": sample_rows[0]["sample_rows"],
    }
    assert "1,10" in sample_rows[0]["sample_rows"]
