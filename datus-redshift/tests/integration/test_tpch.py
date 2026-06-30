# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os

import pytest

from datus_redshift import RedshiftConnector

from .conftest import TPCH_SCHEMA

pytestmark = pytest.mark.skipif(
    not os.getenv("REDSHIFT_HOST"),
    reason="Redshift credentials not available in environment variables",
)

# Schema-qualified table names for Redshift (uses double-quoted identifiers)
S = TPCH_SCHEMA

# ==================== Metadata Tests ====================


@pytest.mark.integration
def test_tpch_get_tables(tpch_setup: RedshiftConnector):
    """Test that TPC-H tables exist in the database."""
    tables = tpch_setup.get_tables()
    db = tpch_setup.database_name
    schema = tpch_setup.schema_name
    expected = {
        f"{db}.{schema}.tpch_region",
        f"{db}.{schema}.tpch_nation",
        f"{db}.{schema}.tpch_customer",
        f"{db}.{schema}.tpch_orders",
        f"{db}.{schema}.tpch_supplier",
    }
    table_set = set(tables)
    assert expected.issubset(table_set), f"Missing tables: {expected - table_set}"


@pytest.mark.integration
def test_tpch_get_columns(tpch_setup: RedshiftConnector):
    """Test getting column schema for tpch_customer table."""
    columns = tpch_setup.get_schema(table_name="tpch_customer")
    assert len(columns) > 0
    column_names = {col["name"] for col in columns}
    assert "custkey" in column_names
    assert "name" in column_names
    assert "nationkey" in column_names
    for col in columns:
        assert "name" in col
        assert "type" in col


@pytest.mark.integration
def test_tpch_get_columns_nation(tpch_setup: RedshiftConnector):
    """Test getting column schema for tpch_nation table."""
    columns = tpch_setup.get_schema(table_name="tpch_nation")
    column_names = {col["name"] for col in columns}
    assert "nationkey" in column_names
    assert "name" in column_names
    assert "regionkey" in column_names


# ==================== Data Query Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_region(tpch_setup: RedshiftConnector):
    """Test querying tpch_region - should have 5 regions."""
    result = tpch_setup.execute(
        {"sql_query": f"SELECT * FROM {S}.tpch_region"},
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 5


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_nation(tpch_setup: RedshiftConnector):
    """Test querying tpch_nation - should have 25 nations."""
    result = tpch_setup.execute(
        {"sql_query": f"SELECT * FROM {S}.tpch_nation"},
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 25


@pytest.mark.integration
def test_tpch_query_join(tpch_setup: RedshiftConnector):
    """Test JOIN query: nation JOIN region."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                f"SELECT n.name AS nation_name, r.name AS region_name "
                f"FROM {S}.tpch_nation n "
                f"JOIN {S}.tpch_region r ON n.regionkey = r.regionkey "
                f"ORDER BY n.nationkey"
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 25
    # ALGERIA is in AFRICA
    first_row = result.sql_return[0]
    assert first_row["nation_name"].strip() == "ALGERIA"
    assert first_row["region_name"].strip() == "AFRICA"


@pytest.mark.integration
def test_tpch_query_aggregation(tpch_setup: RedshiftConnector):
    """Test aggregation: count nations per region."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                f"SELECT r.name AS region_name, COUNT(n.nationkey) AS nation_count "
                f"FROM {S}.tpch_region r "
                f"JOIN {S}.tpch_nation n ON r.regionkey = n.regionkey "
                f"GROUP BY r.name "
                f"ORDER BY r.name"
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == 5  # 5 regions
    total_nations = sum(row["nation_count"] for row in result.sql_return)
    assert total_nations == 25  # 25 nations total


@pytest.mark.integration
def test_tpch_query_customer_orders(tpch_setup: RedshiftConnector):
    """Test JOIN query: customer JOIN orders."""
    result = tpch_setup.execute(
        {
            "sql_query": (
                f"SELECT c.name, COUNT(o.orderkey) AS order_count, "
                f"SUM(o.totalprice) AS total_spent "
                f"FROM {S}.tpch_customer c "
                f"JOIN {S}.tpch_orders o ON c.custkey = o.custkey "
                f"GROUP BY c.name "
                f"ORDER BY order_count DESC "
                f"LIMIT 5"
            )
        },
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) > 0
    assert "order_count" in result.sql_return[0]
    assert "total_spent" in result.sql_return[0]


@pytest.mark.integration
def test_tpch_query_csv_format(tpch_setup: RedshiftConnector):
    """Test CSV result format with TPC-H data."""
    result = tpch_setup.execute(
        {"sql_query": f"SELECT regionkey, name FROM {S}.tpch_region ORDER BY regionkey"},
        result_format="csv",
    )
    assert result.success
    assert "AFRICA" in result.sql_return
    assert "ASIA" in result.sql_return


@pytest.mark.integration
def test_tpch_query_arrow_format(tpch_setup: RedshiftConnector):
    """Test Arrow result format with TPC-H data."""
    result = tpch_setup.execute(
        {"sql_query": f"SELECT regionkey, name FROM {S}.tpch_region ORDER BY regionkey"},
        result_format="arrow",
    )
    assert result.success
    assert result.row_count == 5


@pytest.mark.integration
def test_tpch_query_pandas_format(tpch_setup: RedshiftConnector):
    """Test Pandas result format with TPC-H data."""
    result = tpch_setup.execute(
        {"sql_query": f"SELECT regionkey, name FROM {S}.tpch_region ORDER BY regionkey"},
        result_format="pandas",
    )
    assert result.success
    assert result.row_count == 5
