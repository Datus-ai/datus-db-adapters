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

from datus_postgresql.tpch_data import ROW_COUNTS, TPCH_TABLES

pytestmark = pytest.mark.integration


class TestTpchDataValidation:
    """Validate that TPC-H sample data was loaded correctly."""

    @pytest.mark.parametrize("table_name,expected", list(zip(TPCH_TABLES, ROW_COUNTS)))
    def test_row_counts(self, tpch_setup, table_name, expected):
        """Every TPC-H table holds exactly the shared fixture's row count."""
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {"sql_query": f'SELECT COUNT(*) AS cnt FROM "{schema}"."{table_name}"'},
            result_format="list",
        )
        assert result.success, result.error
        assert result.sql_return[0]["cnt"] == expected


class TestTpchQueries:
    """Run TPC-H-style analytical queries."""

    def test_region_nation_join(self, tpch_setup):
        """Join region and nation tables."""
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT r."name" AS region, COUNT(*) AS nation_count
                    FROM "{schema}"."tpch_region" r
                    JOIN "{schema}"."tpch_nation" n ON r."regionkey" = n."regionkey"
                    GROUP BY r."name"
                    ORDER BY nation_count DESC
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 5
        total = sum(row["nation_count"] for row in result.sql_return)
        assert total == 25

    def test_customer_order_summary(self, tpch_setup):
        """Aggregate orders by customer."""
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT c."name" AS name,
                           COUNT(o."orderkey") AS order_count,
                           SUM(o."totalprice") AS total_spent
                    FROM "{schema}"."tpch_customer" c
                    JOIN "{schema}"."tpch_orders" o ON c."custkey" = o."custkey"
                    GROUP BY c."name"
                    ORDER BY total_spent DESC
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        # All 10 customers in the shared dataset own at least one of the 15 orders.
        assert len(result.sql_return) == 10
        assert sum(row["order_count"] for row in result.sql_return) == 15
        for row in result.sql_return:
            assert row["order_count"] > 0
            assert float(row["total_spent"]) > 0

    def test_supplier_nation_region(self, tpch_setup):
        """Three-table join: supplier -> nation -> region."""
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT s."name" AS supplier, n."name" AS nation, r."name" AS region
                    FROM "{schema}"."tpch_supplier" s
                    JOIN "{schema}"."tpch_nation" n ON s."nationkey" = n."nationkey"
                    JOIN "{schema}"."tpch_region" r ON n."regionkey" = r."regionkey"
                    ORDER BY s."suppkey"
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 5
        # Supplier nationkeys 0, 1, 8, 18, 24 map to these nations and regions.
        assert [row["nation"] for row in result.sql_return] == [
            "ALGERIA",
            "ARGENTINA",
            "INDIA",
            "CHINA",
            "UNITED STATES",
        ]
        assert [row["region"] for row in result.sql_return] == [
            "AFRICA",
            "AMERICA",
            "ASIA",
            "ASIA",
            "AMERICA",
        ]


class TestTpchResultFormats:
    """The same TPC-H query survives every supported result format."""

    def test_csv_format(self, tpch_setup):
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {"sql_query": f'SELECT "regionkey", "name" FROM "{schema}"."tpch_region" ORDER BY "regionkey"'},
            result_format="csv",
        )
        assert result.success, result.error
        assert "AFRICA" in result.sql_return
        assert "MIDDLE EAST" in result.sql_return

    def test_arrow_format(self, tpch_setup):
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {"sql_query": f'SELECT "regionkey", "name" FROM "{schema}"."tpch_region" ORDER BY "regionkey"'},
            result_format="arrow",
        )
        assert result.success, result.error
        assert result.sql_return.num_rows == 5
        assert "regionkey" in result.sql_return.column_names

    def test_pandas_format(self, tpch_setup):
        schema = tpch_setup.schema_name
        result = tpch_setup.execute(
            {"sql_query": f'SELECT "regionkey", "name" FROM "{schema}"."tpch_region" ORDER BY "regionkey"'},
            result_format="pandas",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 5
        assert "name" in result.sql_return.columns


class TestTpchMetadata:
    """Validate metadata retrieval for TPC-H tables."""

    def test_get_tables_includes_tpch(self, tpch_setup):
        """get_tables() should return TPC-H tables qualified with the database name.

        Only ``schema_name`` is passed, so the listing prefixes the unscoped database
        level, yielding ``<database>.<table>``.
        """
        schema = tpch_setup.schema_name
        tables = tpch_setup.get_tables(schema_name=schema)
        db = tpch_setup.database_name
        expected = {f"{db}.{table}" for table in TPCH_TABLES}
        assert expected.issubset(set(tables))

    def test_get_schema_columns(self, tpch_setup):
        """get_schema() should return correct columns for tpch_region."""
        schema = tpch_setup.schema_name
        cols = tpch_setup.get_schema(schema_name=schema, table_name="tpch_region")
        assert [c["name"] for c in cols] == ["regionkey", "name", "comment"]

    def test_get_tables_with_ddl(self, tpch_setup):
        """get_tables_with_ddl() should include DDL for TPC-H tables."""
        schema = tpch_setup.schema_name
        tables_ddl = tpch_setup.get_tables_with_ddl(schema_name=schema)
        tpch_ddl = [t for t in tables_ddl if t["table_name"].startswith("tpch_")]
        assert len(tpch_ddl) >= 5
        for item in tpch_ddl:
            assert "definition" in item
            assert "CREATE TABLE" in item["definition"]
