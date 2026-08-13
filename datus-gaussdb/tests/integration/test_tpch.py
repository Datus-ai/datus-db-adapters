# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_gaussdb import GaussDBConnector

# ==================== Metadata ====================


@pytest.mark.integration
def test_tpch_get_tables(tpch_setup: GaussDBConnector):
    """All TPC-H tables are visible through get_tables."""
    tables = set(tpch_setup.get_tables(schema_name="public"))
    db = tpch_setup.database_name
    expected = {
        f"{db}.tpch_region",
        f"{db}.tpch_nation",
        f"{db}.tpch_customer",
        f"{db}.tpch_orders",
        f"{db}.tpch_supplier",
    }

    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


@pytest.mark.integration
def test_tpch_get_columns(tpch_setup: GaussDBConnector):
    """Column metadata for tpch_customer carries names and types."""
    columns = tpch_setup.get_schema(schema_name="public", table_name="tpch_customer")

    column_names = {column["name"] for column in columns}
    assert {"custkey", "name", "nationkey", "acctbal", "mktsegment"}.issubset(column_names)
    for column in columns:
        assert column["type"]


@pytest.mark.integration
def test_tpch_get_tables_with_ddl(tpch_setup: GaussDBConnector):
    """DDL is reconstructed for every TPC-H table."""
    tables_ddl = [
        t for t in tpch_setup.get_tables_with_ddl(schema_name="public") if t["table_name"].startswith("tpch_")
    ]

    assert len(tables_ddl) >= 5
    for item in tables_ddl:
        assert "CREATE TABLE" in item["definition"]


# ==================== Data Queries ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_region(tpch_setup: GaussDBConnector):
    """tpch_region holds the 5 TPC-H regions."""
    result = tpch_setup.execute_query('SELECT * FROM "tpch_region"', result_format="list")

    assert result.success, result.error
    assert len(result.sql_return) == 5


@pytest.mark.integration
@pytest.mark.acceptance
def test_tpch_query_nation(tpch_setup: GaussDBConnector):
    """tpch_nation holds the 25 TPC-H nations."""
    result = tpch_setup.execute_query('SELECT * FROM "tpch_nation"', result_format="list")

    assert result.success, result.error
    assert len(result.sql_return) == 25


@pytest.mark.integration
def test_tpch_query_join(tpch_setup: GaussDBConnector):
    """nation JOIN region resolves every nation to its region."""
    result = tpch_setup.execute_query(
        'SELECT n."name" AS nation_name, r."name" AS region_name '
        'FROM "tpch_nation" n '
        'JOIN "tpch_region" r ON n."regionkey" = r."regionkey" '
        'ORDER BY n."nationkey"',
        result_format="list",
    )

    assert result.success, result.error
    assert len(result.sql_return) == 25
    assert result.sql_return[0]["nation_name"] == "ALGERIA"
    assert result.sql_return[0]["region_name"] == "AFRICA"


@pytest.mark.integration
def test_tpch_query_aggregation(tpch_setup: GaussDBConnector):
    """GROUP BY across the join accounts for all 25 nations."""
    result = tpch_setup.execute_query(
        'SELECT r."name" AS region_name, COUNT(n."nationkey") AS nation_count '
        'FROM "tpch_region" r '
        'JOIN "tpch_nation" n ON r."regionkey" = n."regionkey" '
        'GROUP BY r."name" '
        'ORDER BY r."name"',
        result_format="list",
    )

    assert result.success, result.error
    assert len(result.sql_return) == 5
    assert sum(row["nation_count"] for row in result.sql_return) == 25


@pytest.mark.integration
def test_tpch_query_customer_orders(tpch_setup: GaussDBConnector):
    """customer JOIN orders aggregates decimals without losing rows."""
    result = tpch_setup.execute_query(
        'SELECT c."name" AS customer_name, COUNT(o."orderkey") AS order_count, '
        'SUM(o."totalprice") AS total_spent '
        'FROM "tpch_customer" c '
        'JOIN "tpch_orders" o ON c."custkey" = o."custkey" '
        'GROUP BY c."name" '
        "ORDER BY order_count DESC, customer_name "
        "LIMIT 5",
        result_format="list",
    )

    assert result.success, result.error
    assert result.sql_return
    first = result.sql_return[0]
    assert first["order_count"] > 0
    assert first["total_spent"] is not None


@pytest.mark.integration
def test_tpch_query_csv_format(tpch_setup: GaussDBConnector):
    """CSV formatting works end to end."""
    result = tpch_setup.execute_query(
        'SELECT "regionkey", "name" FROM "tpch_region" ORDER BY "regionkey"',
        result_format="csv",
    )

    assert result.success, result.error
    assert "AFRICA" in result.sql_return
    assert "ASIA" in result.sql_return
