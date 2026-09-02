# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live Snowflake tests.

See ``conftest.py`` for the environment variables the fixtures read. The whole module is skipped
when the credentials are absent. Once a connection succeeds every fixture failure is raised,
never skipped, so a green run means the adapter actually works.
"""

import pytest

from datus_snowflake import SnowflakeConfig, SnowflakeConnector

from .conftest import MetadataObjects, requires_live_credentials

pytestmark = [pytest.mark.integration, requires_live_credentials]


# ==================== Connection Tests ====================


def test_connection_with_config_object(config: SnowflakeConfig):
    """Test connection using config object."""
    conn = SnowflakeConnector(config)
    result = conn.test_connection()
    assert result["success"] is True
    conn.close()


def test_connection_with_dict(config_dict: dict):
    """Test connection using dict config."""
    conn = SnowflakeConnector(config_dict)
    result = conn.test_connection()
    assert result["success"] is True
    conn.close()


# ==================== Database Tests ====================


def test_get_databases(connector: SnowflakeConnector):
    """Test getting list of databases."""
    databases = connector.get_databases()
    assert isinstance(databases, list)
    assert len(databases) > 0


def test_get_databases_exclude_system(connector: SnowflakeConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"SNOWFLAKE"}
    for db in databases:
        assert db.upper() not in system_dbs


# ==================== Schema Tests (SchemaNamespaceMixin) ====================


def test_get_schemas(connector: SnowflakeConnector, database_name: str):
    """Test getting list of schemas."""
    schemas = connector.get_schemas(database_name=database_name)
    assert isinstance(schemas, list)


def test_get_schemas_exclude_system(connector: SnowflakeConnector, database_name: str):
    """Test that system schemas are excluded by default."""
    schemas = connector.get_schemas(database_name=database_name, include_sys=False)
    for schema in schemas:
        assert schema.upper() != "INFORMATION_SCHEMA"


# ==================== Table Metadata Tests ====================


def test_get_tables(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture table is listed, schema-qualified when the caller scopes only the database."""
    assert f"{metadata_objects.schema}.{metadata_objects.table}" in connector.get_tables(
        database_name=metadata_objects.database
    )
    assert metadata_objects.table in connector.get_tables(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_tables_with_ddl(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture table comes back with full coordinates and its real DDL."""
    tables = connector.get_tables_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in tables if item["table_name"] == metadata_objects.table]
    assert len(matches) == 1, f"{metadata_objects.table} missing from {[item['table_name'] for item in tables]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.table,
        "table_type": "table",
        "identifier": metadata_objects.identifier(metadata_objects.table),
    }
    assert "CREATE" in definition.upper()
    assert "TABLE" in definition.upper()
    assert metadata_objects.table in definition


# ==================== View Tests ====================


def test_get_views(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture view is listed, schema-qualified when the caller scopes only the database."""
    assert f"{metadata_objects.schema}.{metadata_objects.view}" in connector.get_views(
        database_name=metadata_objects.database
    )
    assert metadata_objects.view in connector.get_views(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_views_with_ddl(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """The fixture view comes back with full coordinates and a DDL naming its base table."""
    views = connector.get_views_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in views if item["table_name"] == metadata_objects.view]
    assert len(matches) == 1, f"{metadata_objects.view} missing from {[item['table_name'] for item in views]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.view,
        "table_type": "view",
        "identifier": metadata_objects.identifier(metadata_objects.view),
    }
    assert "CREATE" in definition.upper()
    assert "VIEW" in definition.upper()
    assert metadata_objects.table in definition


# ==================== Materialized View Tests (MaterializedViewSupportMixin) ====================


def test_get_materialized_views(
    connector: SnowflakeConnector,
    metadata_objects: MetadataObjects,
    materialized_view: str,
):
    """The fixture materialized view is listed under both scoping depths."""
    assert f"{metadata_objects.schema}.{materialized_view}" in connector.get_materialized_views(
        database_name=metadata_objects.database
    )
    assert materialized_view in connector.get_materialized_views(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )


def test_get_materialized_views_with_ddl(
    connector: SnowflakeConnector,
    metadata_objects: MetadataObjects,
    materialized_view: str,
):
    """The fixture materialized view comes back typed ``mv`` with its real DDL."""
    mvs = connector.get_materialized_views_with_ddl(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
    )

    matches = [item for item in mvs if item["table_name"] == materialized_view]
    assert len(matches) == 1, f"{materialized_view} missing from {[item['table_name'] for item in mvs]}"

    entry = matches[0]
    definition = entry.pop("definition")
    assert entry == {
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": materialized_view,
        "table_type": "mv",
        "identifier": metadata_objects.identifier(materialized_view),
    }
    assert "CREATE" in definition.upper()
    assert "MATERIALIZED VIEW" in definition.upper()
    assert metadata_objects.table in definition


# ==================== Schema Structure Tests ====================


def test_get_schema(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """Every column of the fixture table is described exactly, plus the trailing table summary."""
    schema = connector.get_schema(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
        table_name=metadata_objects.table,
    )

    assert schema == [
        {
            "cid": 0,
            "name": "ID",
            "type": "NUMBER(9,0)",
            "nullable": False,
            "pk": False,
            "default_value": None,
            "comment": "row id",
        },
        {
            "cid": 1,
            "name": "VALUE",
            "type": "VARCHAR(64)",
            "nullable": True,
            "pk": False,
            "default_value": None,
            "comment": "row label",
        },
        {
            "table": metadata_objects.table,
            "columns": [
                {"name": "ID", "type": "NUMBER(9,0)"},
                {"name": "VALUE", "type": "VARCHAR(64)"},
            ],
            "table_type": "table",
        },
    ]


# ==================== Sample Data Tests ====================


def test_get_sample_rows(connector: SnowflakeConnector, metadata_objects: MetadataObjects):
    """Sampling the fixture table returns its two known rows as CSV."""
    sample_rows = connector.get_sample_rows(
        database_name=metadata_objects.database,
        schema_name=metadata_objects.schema,
        tables=[metadata_objects.table],
        top_n=3,
    )

    assert len(sample_rows) == 1
    assert sample_rows[0] == {
        "identifier": metadata_objects.identifier(metadata_objects.table),
        "catalog_name": "",
        "database_name": metadata_objects.database,
        "schema_name": metadata_objects.schema,
        "table_name": metadata_objects.table,
        "table_type": "table",
        "sample_rows": sample_rows[0]["sample_rows"],
    }

    csv_lines = sample_rows[0]["sample_rows"].strip().split("\n")
    assert csv_lines[0] == "ID,VALUE"
    assert sorted(csv_lines[1:]) == ["1,alpha", "2,beta"]


# ==================== SQL Execution Tests ====================


def test_execute_query_csv(connector: SnowflakeConnector):
    """Test executing query with CSV format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="csv")
    assert result.success
    assert not result.error
    assert "num" in result.sql_return


def test_execute_query_list(connector: SnowflakeConnector):
    """Test executing query with list format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


def test_execute_query_arrow(connector: SnowflakeConnector):
    """Test executing query with Arrow format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="arrow")
    assert result.success
    assert not result.error
    assert result.sql_return is not None


def test_execute_query_pandas(connector: SnowflakeConnector):
    """Test executing query with pandas format."""
    result = connector.execute_query('SELECT 1 AS "num"', result_format="pandas")
    assert result.success
    assert not result.error
    assert len(result.sql_return) == 1


def test_execute_show_databases(connector: SnowflakeConnector):
    """Test executing SHOW DATABASES."""
    result = connector.execute_query("SHOW DATABASES", result_format="list")
    assert result.success
    assert isinstance(result.sql_return, list)


def test_execute_show_schemas(connector: SnowflakeConnector, database_name: str):
    """Test executing SHOW SCHEMAS."""
    result = connector.execute_query(f'SHOW SCHEMAS IN DATABASE "{database_name}"', result_format="list")
    assert result.success
    assert isinstance(result.sql_return, list)


# ==================== Error Handling Tests ====================


def test_execute_invalid_sql(connector: SnowflakeConnector):
    """Test exception on invalid SQL."""
    result = connector.execute_query("INVALID SQL SYNTAX")
    assert not result.success
    assert result.error is not None


def test_execute_nonexistent_table(connector: SnowflakeConnector):
    """Test exception on non-existent table."""
    result = connector.execute_query("SELECT * FROM nonexistent_table_xyz")
    assert not result.success
    assert result.error is not None
