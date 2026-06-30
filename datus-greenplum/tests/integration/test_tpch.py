# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_greenplum import GreenplumConnector

# ==================== Metadata Tests ====================


@pytest.mark.integration
def test_tpch_get_tables(tpch_setup: GreenplumConnector):
    """Test that TPC-H tables exist in the database."""
    tables = tpch_setup.get_tables(schema_name="public")
    db = tpch_setup.database_name
    expected = {
        f"{db}.tpch_region",
        f"{db}.tpch_nation",
        f"{db}.tpch_customer",
        f"{db}.tpch_orders",
        f"{db}.tpch_supplier",
    }
    table_set = set(tables)
    assert expected.issubset(table_set), f"Missing tables: {expected - table_set}"


@pytest.mark.integration
def test_tpch_get_columns(tpch_setup: GreenplumConnector):
    """Test getting column schema for tpch_customer table."""
    columns = tpch_setup.get_schema(schema_name="public", table_name="tpch_customer")
    assert len(columns) > 0
    column_names = {col["name"] for col in columns}
    assert "custkey" in column_names
    assert "name" in column_names
    assert "nationkey" in column_names
    for col in columns:
        assert "name" in col
        assert "type" in col


@pytest.mark.integration
def test_tpch_get_columns_nation(tpch_setup: GreenplumConnector):
    """Test getting column schema for tpch_nation table."""
    columns = tpch_setup.get_schema(schema_name="public", table_name="tpch_nation")
    column_names = {col["name"] for col in columns}
    assert "nationkey" in column_names
    assert "name" in column_names
    assert "regionkey" in column_names


# ==================== Data Query Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_region(tpch_setup: GreenplumConnector):
    """Test querying tpch_region - should have 5 regions."""
    result = tpch_setup.execute(
        {"sql_query": 'SELECT * FROM "tpch_region"'},
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 5


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_nation(tpch_setup: GreenplumConnector):
    """Test querying tpch_nation - should have 25 nations."""
    result = tpch_setup.execute(
        {"sql_query": 'SELECT * FROM "tpch_nation"'},
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 25


@pytest.mark.integration
def test_tpch_query_join(tpch_setup: GreenplumConnector):
    """Test JOIN query: nation JOIN region."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                'SELECT n."name" AS nation_name, r."name" AS region_name '
                'FROM "tpch_nation" n '
                'JOIN "tpch_region" r ON n."regionkey" = r."regionkey" '
                'ORDER BY n."nationkey"'
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 25
    # ALGERIA is in AFRICA
    first_row = result.sql_return[0]
    assert first_row["nation_name"] == "ALGERIA"
    assert first_row["region_name"] == "AFRICA"


@pytest.mark.integration
def test_tpch_query_aggregation(tpch_setup: GreenplumConnector):
    """Test aggregation: count nations per region."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                'SELECT r."name" AS region_name, COUNT(n."nationkey") AS nation_count '
                'FROM "tpch_region" r '
                'JOIN "tpch_nation" n ON r."regionkey" = n."regionkey" '
                'GROUP BY r."name" '
                'ORDER BY r."name"'
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 5  # 5 regions
    total_nations = sum(row["nation_count"] for row in result.sql_return)
    assert total_nations == 25  # 25 nations total


@pytest.mark.integration
def test_tpch_query_customer_orders(tpch_setup: GreenplumConnector):
    """Test JOIN query: customer JOIN orders."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                'SELECT c."name", COUNT(o."orderkey") AS order_count, '
                'SUM(o."totalprice") AS total_spent '
                'FROM "tpch_customer" c '
                'JOIN "tpch_orders" o ON c."custkey" = o."custkey" '
                'GROUP BY c."name" '
                "ORDER BY order_count DESC "
                "LIMIT 5"
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) > 0
    assert "order_count" in result.sql_return[0]
    assert "total_spent" in result.sql_return[0]


@pytest.mark.integration
def test_tpch_query_csv_format(tpch_setup: GreenplumConnector):
    """Test CSV result format with TPC-H data."""
    result = tpch_setup.execute(
        {"sql_query": 'SELECT "regionkey", "name" FROM "tpch_region" ORDER BY "regionkey"'},
        result_format="csv",
    )
    assert result.success
    assert "AFRICA" in result.sql_return
    assert "ASIA" in result.sql_return


@pytest.mark.integration
def test_tpch_get_tables_with_ddl(tpch_setup: GreenplumConnector):
    """get_tables_with_ddl() should include DDL with distribution policy for TPC-H tables."""
    tables_ddl = tpch_setup.get_tables_with_ddl(schema_name="public")
    tpch_ddl = [t for t in tables_ddl if t["table_name"].startswith("tpch_")]
    assert len(tpch_ddl) >= 5
    for item in tpch_ddl:
        assert "definition" in item
        assert "CREATE TABLE" in item["definition"]
        # Greenplum DDL should include distribution policy
        ddl = item["definition"]
        assert "DISTRIBUTED BY" in ddl or "DISTRIBUTED RANDOMLY" in ddl, (
            f"DDL for {item['table_name']} missing distribution policy: {ddl}"
        )
