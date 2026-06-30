# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""TPC-H integration tests for PostgreSQL adapter.

These tests require a running PostgreSQL instance with valid credentials.
The tpch_setup fixture (session-scoped) creates and populates TPC-H tables
before the first test and drops them after the last test.

Run with:
    pytest tests/integration/test_tpch.py -v
"""

import pytest

pytestmark = pytest.mark.integration


class TestTpchDataValidation:
    """Validate that TPC-H sample data was loaded correctly."""

    def test_region_count(self, tpch_setup):
        """Verify tpch_region has 5 rows."""
        result = tpch_setup.execute(
            {"sql_query": 'SELECT COUNT(*) AS cnt FROM "public"."tpch_region"'},
            result_format="list",
        )
        assert result.sql_return[0]["cnt"] == 5

    def test_nation_count(self, tpch_setup):
        """Verify tpch_nation has 25 rows."""
        result = tpch_setup.execute(
            {"sql_query": 'SELECT COUNT(*) AS cnt FROM "public"."tpch_nation"'},
            result_format="list",
        )
        assert result.sql_return[0]["cnt"] == 25

    def test_customer_count(self, tpch_setup):
        """Verify tpch_customer has 10 rows."""
        result = tpch_setup.execute(
            {"sql_query": 'SELECT COUNT(*) AS cnt FROM "public"."tpch_customer"'},
            result_format="list",
        )
        assert result.sql_return[0]["cnt"] == 10

    def test_orders_count(self, tpch_setup):
        """Verify tpch_orders has 15 rows."""
        result = tpch_setup.execute(
            {"sql_query": 'SELECT COUNT(*) AS cnt FROM "public"."tpch_orders"'},
            result_format="list",
        )
        assert result.sql_return[0]["cnt"] == 15

    def test_supplier_count(self, tpch_setup):
        """Verify tpch_supplier has 5 rows."""
        result = tpch_setup.execute(
            {"sql_query": 'SELECT COUNT(*) AS cnt FROM "public"."tpch_supplier"'},
            result_format="list",
        )
        assert result.sql_return[0]["cnt"] == 5


class TestTpchQueries:
    """Run TPC-H-style analytical queries."""

    def test_region_nation_join(self, tpch_setup):
        """Join region and nation tables."""
        result = tpch_setup.execute(
            {
                "sql_query": """
                    SELECT r."r_name" AS region, COUNT(*) AS nation_count
                    FROM "public"."tpch_region" r
                    JOIN "public"."tpch_nation" n ON r."r_regionkey" = n."n_regionkey"
                    GROUP BY r."r_name"
                    ORDER BY nation_count DESC
                """
            },
            result_format="list",
        )
        assert len(result.sql_return) == 5
        total = sum(row["nation_count"] for row in result.sql_return)
        assert total == 25

    def test_customer_order_summary(self, tpch_setup):
        """Aggregate orders by customer."""
        result = tpch_setup.execute(
            {
                "sql_query": """
                    SELECT c."c_name",
                           COUNT(o."o_orderkey") AS order_count,
                           SUM(o."o_totalprice") AS total_spent
                    FROM "public"."tpch_customer" c
                    JOIN "public"."tpch_orders" o ON c."c_custkey" = o."o_custkey"
                    GROUP BY c."c_name"
                    ORDER BY total_spent DESC
                """
            },
            result_format="list",
        )
        assert len(result.sql_return) > 0
        for row in result.sql_return:
            assert row["order_count"] > 0
            assert float(row["total_spent"]) > 0

    def test_supplier_nation_region(self, tpch_setup):
        """Three-table join: supplier -> nation -> region."""
        result = tpch_setup.execute(
            {
                "sql_query": """
                    SELECT s."s_name", n."n_name" AS nation, r."r_name" AS region
                    FROM "public"."tpch_supplier" s
                    JOIN "public"."tpch_nation" n ON s."s_nationkey" = n."n_nationkey"
                    JOIN "public"."tpch_region" r ON n."n_regionkey" = r."r_regionkey"
                    ORDER BY s."s_suppkey"
                """
            },
            result_format="list",
        )
        assert len(result.sql_return) == 5
        for row in result.sql_return:
            assert row["s_name"] is not None
            assert row["nation"] is not None
            assert row["region"] is not None


class TestTpchMetadata:
    """Validate metadata retrieval for TPC-H tables."""

    def test_get_tables_includes_tpch(self, tpch_setup):
        """get_tables() should return TPC-H tables qualified with the database name.

        Only ``schema_name`` is passed, so the listing prefixes the unscoped database
        level, yielding ``<database>.<table>``.
        """
        tables = tpch_setup.get_tables(schema_name="public")
        db = tpch_setup.database_name
        expected = {
            f"{db}.tpch_region",
            f"{db}.tpch_nation",
            f"{db}.tpch_supplier",
            f"{db}.tpch_customer",
            f"{db}.tpch_orders",
        }
        assert expected.issubset(set(tables))

    def test_get_schema_columns(self, tpch_setup):
        """get_schema() should return correct columns for tpch_region."""
        cols = tpch_setup.get_schema(schema_name="public", table_name="tpch_region")
        col_names = [c["name"] for c in cols]
        assert "r_regionkey" in col_names
        assert "r_name" in col_names
        assert "r_comment" in col_names

    def test_get_tables_with_ddl(self, tpch_setup):
        """get_tables_with_ddl() should include DDL for TPC-H tables."""
        tables_ddl = tpch_setup.get_tables_with_ddl(schema_name="public")
        tpch_ddl = [t for t in tables_ddl if t["table_name"].startswith("tpch_")]
        assert len(tpch_ddl) >= 5
        for item in tpch_ddl:
            assert "definition" in item
            assert "CREATE TABLE" in item["definition"]
