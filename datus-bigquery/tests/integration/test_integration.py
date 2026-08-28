# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_bigquery import BigQueryConfig, BigQueryConnector
from datus_db_core.testing.contract import assert_success

# ==================== Connection Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: BigQueryConfig):
    """Test connection using config object."""
    conn = BigQueryConnector(config)
    try:
        assert conn.test_connection()
    finally:
        conn.close()


# ==================== Database (Dataset) Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: BigQueryConnector):
    """Test getting list of datasets."""
    databases = connector.get_databases()
    assert isinstance(databases, list)
    assert len(databases) > 0


@pytest.mark.integration
def test_get_databases_exclude_system(connector: BigQueryConnector):
    """Test that system datasets are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"INFORMATION_SCHEMA", "information_schema"}
    for db in databases:
        assert db not in system_dbs


@pytest.mark.integration
def test_get_schemas_returns_empty(connector: BigQueryConnector):
    """Test that get_schemas returns empty list (BigQuery has no schema layer)."""
    schemas = connector.get_schemas()
    assert schemas == []


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: BigQueryConnector, config: BigQueryConfig):
    """Test getting table list."""
    tables = connector.get_tables(
        catalog_name=config.project,
        database_name=config.dataset,
    )
    assert isinstance(tables, list)


@pytest.mark.integration
def test_get_tables_with_ddl(connector: BigQueryConnector, config: BigQueryConfig):
    """Test getting tables with DDL."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"

    full = f"`{config.project}`.`{config.dataset}`.`{table_name}`"
    assert_success(connector.execute_ddl(f"CREATE TABLE {full} (id INT64, name STRING)"), "create DDL table")

    try:
        tables = connector.get_tables_with_ddl(
            catalog_name=config.project,
            database_name=config.dataset,
            tables=[table_name],
        )

        assert len(tables) > 0, f"Expected to find table {table_name}"
        table = tables[0]
        assert table["table_name"] == table_name
        assert "definition" in table
        assert table["table_type"] == "table"
        assert "database_name" in table
        assert table["schema_name"] == ""
        assert "identifier" in table
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full}")


# ==================== View Tests ====================


@pytest.mark.integration
def test_get_views_with_ddl(connector: BigQueryConnector, config: BigQueryConfig):
    """Test getting views with DDL."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"
    view_name = f"test_view_{suffix}"

    full_table = f"`{config.project}`.`{config.dataset}`.`{table_name}`"
    full_view = f"`{config.project}`.`{config.dataset}`.`{view_name}`"

    # Create base table
    assert_success(
        connector.execute_ddl(f"CREATE TABLE {full_table} (id INT64, name STRING)"),
        "create view base table",
    )

    # Create view
    assert_success(
        connector.execute_ddl(f"CREATE VIEW {full_view} AS SELECT * FROM {full_table}"),
        "create view",
    )

    try:
        views = connector.get_views_with_ddl(
            catalog_name=config.project,
            database_name=config.dataset,
        )

        assert len(views) > 0, f"Expected to find view {view_name}"
        view = [v for v in views if v["table_name"] == view_name]
        assert view, f"Expected view {view_name} in results"
        assert "definition" in view[0]
        assert view[0]["table_type"] == "view"
        assert view_name in connector.get_views(config.project, config.dataset)
        assert view_name not in connector.get_tables(config.project, config.dataset)
    finally:
        connector.execute_ddl(f"DROP VIEW IF EXISTS {full_view}")
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full_table}")


@pytest.mark.integration
def test_get_materialized_views_with_ddl(connector: BigQueryConnector, config: BigQueryConfig):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_mv_table_{suffix}"
    view_name = f"test_mv_{suffix}"
    full_table = connector.full_name(config.project, config.dataset, table_name=table_name)
    full_view = connector.full_name(config.project, config.dataset, table_name=view_name)

    assert_success(
        connector.execute_ddl(f"CREATE TABLE {full_table} (id INT64 NOT NULL)"),
        "create materialized view base table",
    )
    assert_success(
        connector.execute_ddl(
            f"CREATE MATERIALIZED VIEW {full_view} AS SELECT id, COUNT(*) AS row_count FROM {full_table} GROUP BY id"
        ),
        "create materialized view",
    )

    try:
        names = connector.get_materialized_views(config.project, config.dataset)
        definitions = connector.get_materialized_views_with_ddl(config.project, config.dataset)
        selected = [item for item in definitions if item["table_name"] == view_name]

        assert view_name in names
        assert selected
        assert selected[0]["table_type"] == "mv"
        assert "CREATE MATERIALIZED VIEW" in selected[0]["definition"].upper()
        assert view_name not in connector.get_tables(config.project, config.dataset)
        assert view_name not in connector.get_views(config.project, config.dataset)
    finally:
        connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS {full_view}")
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full_table}")


# ==================== Schema Tests ====================


@pytest.mark.integration
def test_get_schema(connector: BigQueryConnector, config: BigQueryConfig):
    """Test getting table schema (columns)."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_schema_{suffix}"

    full = f"`{config.project}`.`{config.dataset}`.`{table_name}`"
    assert_success(
        connector.execute_ddl(
            f"""
        CREATE TABLE {full} (
            id INT64 NOT NULL,
            name STRING,
            amount FLOAT64,
            created_at TIMESTAMP,
            is_active BOOL,
            tags ARRAY<STRING>
        )
    """
        ),
        "create schema test table",
    )

    try:
        schema = connector.get_schema(
            catalog_name=config.project,
            database_name=config.dataset,
            table_name=table_name,
        )

        assert len(schema) == 6

        # Check id column
        id_col = next(col for col in schema if col["name"] == "id")
        assert id_col["nullable"] is False
        assert "int64" in id_col["type"].lower()

        # Check name column
        name_col = next(col for col in schema if col["name"] == "name")
        assert name_col["nullable"] is True
        assert "string" in name_col["type"].lower()
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full}")


# ==================== Sample Data Tests ====================


@pytest.mark.integration
def test_get_sample_rows(connector: BigQueryConnector, config: BigQueryConfig):
    """Test getting sample rows."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_sample_{suffix}"

    full = f"`{config.project}`.`{config.dataset}`.`{table_name}`"
    assert_success(
        connector.execute_ddl(f"CREATE TABLE {full} (id INT64, name STRING)"),
        "create sample table",
    )

    # Insert test data
    assert_success(
        connector.execute_insert(
            f"""
        INSERT INTO {full} (id, name) VALUES
        (1, 'Alice'),
        (2, 'Bob'),
        (3, 'Charlie')
    """
        ),
        "insert sample rows",
    )

    try:
        sample_rows = connector.get_sample_rows(
            catalog_name=config.project,
            database_name=config.dataset,
            tables=[table_name],
            top_n=2,
        )

        assert len(sample_rows) == 1
        assert sample_rows[0]["table_name"] == table_name
        assert "sample_rows" in sample_rows[0]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full}")


# ==================== SQL Execution Tests ====================


@pytest.mark.integration
def test_execute_select(connector: BigQueryConnector):
    """Test executing SELECT query."""
    result = connector.execute({"sql_query": "SELECT 1 as num"}, result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
def test_execute_ddl(connector: BigQueryConnector, config: BigQueryConfig):
    """Test DDL operations."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_ddl_{suffix}"
    full = f"`{config.project}`.`{config.dataset}`.`{table_name}`"

    try:
        # CREATE
        create_result = connector.execute_ddl(f"CREATE TABLE {full} (id INT64, name STRING)")
        assert create_result.success

    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {full}")


# ==================== Error Handling Tests ====================


@pytest.mark.integration
def test_exception_on_nonexistent_table(connector: BigQueryConnector):
    """Test exception on non-existent table."""
    result = connector.execute(
        {"sql_query": f"SELECT * FROM `nonexistent_dataset`.`nonexistent_table_{uuid.uuid4().hex}`"}
    )
    assert result.error


# ==================== Utility Tests ====================


@pytest.mark.integration
def test_full_name(connector: BigQueryConnector):
    """Test full_name with project and dataset."""
    full_name = connector.full_name(
        catalog_name="my-project",
        database_name="my_dataset",
        table_name="my_table",
    )
    assert full_name == "`my-project`.`my_dataset`.`my_table`"


@pytest.mark.integration
def test_identifier(connector: BigQueryConnector):
    """Test identifier generation."""
    identifier = connector.identifier(
        catalog_name="my-project",
        database_name="my_dataset",
        table_name="my_table",
    )
    assert identifier == "my-project.my_dataset.my_table"


@pytest.mark.integration
def test_get_type(connector: BigQueryConnector):
    """Test get_type returns bigquery."""
    assert connector.get_type() == "bigquery"
