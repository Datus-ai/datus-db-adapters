# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""TPC-H integration tests for the Oracle adapter.

These tests require a running Oracle instance (see docker-compose.yml).
The tpch_setup fixture (session-scoped) creates and populates TPC-H tables
before the first test and drops them after the last test.

Run with:
    pytest tests/integration/test_tpch.py -v
"""

import os

import pytest

pytestmark = pytest.mark.integration

SCHEMA = os.getenv("ORACLE_SCHEMA", "DATUS_TEST")


class TestTpchDataValidation:
    """Validate that TPC-H sample data was loaded correctly."""

    @pytest.mark.parametrize(
        "table_name,expected",
        [
            ("TPCH_REGION", 5),
            ("TPCH_NATION", 25),
            ("TPCH_SUPPLIER", 5),
            ("TPCH_CUSTOMER", 10),
            ("TPCH_ORDERS", 15),
        ],
    )
    def test_row_counts(self, tpch_setup, table_name, expected):
        result = tpch_setup.execute(
            {"sql_query": f'SELECT COUNT(*) AS cnt FROM "{SCHEMA}"."{table_name}"'},
            result_format="list",
        )
        assert result.success, result.error
        assert result.sql_return[0]["cnt"] == expected


class TestTpchQueries:
    """Run TPC-H-style analytical queries."""

    def test_region_nation_join(self, tpch_setup):
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT r."R_NAME" AS region, COUNT(*) AS nation_count
                    FROM "{SCHEMA}"."TPCH_REGION" r
                    JOIN "{SCHEMA}"."TPCH_NATION" n ON r."R_REGIONKEY" = n."N_REGIONKEY"
                    GROUP BY r."R_NAME"
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
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT c."C_NAME" AS c_name,
                           COUNT(o."O_ORDERKEY") AS order_count,
                           SUM(o."O_TOTALPRICE") AS total_spent
                    FROM "{SCHEMA}"."TPCH_CUSTOMER" c
                    JOIN "{SCHEMA}"."TPCH_ORDERS" o ON c."C_CUSTKEY" = o."O_CUSTKEY"
                    GROUP BY c."C_NAME"
                    ORDER BY total_spent DESC
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) > 0
        for row in result.sql_return:
            assert row["order_count"] > 0
            assert float(row["total_spent"]) > 0

    def test_supplier_nation_region(self, tpch_setup):
        """Three-table join: supplier -> nation -> region."""
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT s."S_NAME" AS s_name, n."N_NAME" AS nation, r."R_NAME" AS region
                    FROM "{SCHEMA}"."TPCH_SUPPLIER" s
                    JOIN "{SCHEMA}"."TPCH_NATION" n ON s."S_NATIONKEY" = n."N_NATIONKEY"
                    JOIN "{SCHEMA}"."TPCH_REGION" r ON n."N_REGIONKEY" = r."R_REGIONKEY"
                    ORDER BY s."S_SUPPKEY"
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 5
        for row in result.sql_return:
            assert row["s_name"] is not None
            assert row["nation"] is not None
            assert row["region"] is not None

    def test_fetch_first_top_n(self, tpch_setup):
        """FETCH FIRST replaces LIMIT for top-N queries."""
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT "O_ORDERKEY" AS o_orderkey, "O_TOTALPRICE" AS o_totalprice
                    FROM "{SCHEMA}"."TPCH_ORDERS"
                    ORDER BY "O_TOTALPRICE" DESC
                    FETCH FIRST 3 ROWS ONLY
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 3
        prices = [float(row["o_totalprice"]) for row in result.sql_return]
        assert prices == sorted(prices, reverse=True)


class TestTpchMetadata:
    """Validate metadata retrieval for TPC-H tables."""

    def test_get_tables_includes_tpch(self, tpch_setup):
        tables = tpch_setup.get_tables(schema_name=SCHEMA)
        expected = {"TPCH_REGION", "TPCH_NATION", "TPCH_SUPPLIER", "TPCH_CUSTOMER", "TPCH_ORDERS"}
        assert expected.issubset(set(tables))

    def test_get_schema_columns(self, tpch_setup):
        cols = tpch_setup.get_schema(schema_name=SCHEMA, table_name="TPCH_REGION")
        col_names = [c["name"] for c in cols]
        assert col_names == ["R_REGIONKEY", "R_NAME", "R_COMMENT"]
        pk_col = [c for c in cols if c["name"] == "R_REGIONKEY"][0]
        assert pk_col["pk"] is True

    def test_get_tables_with_ddl(self, tpch_setup):
        tables_ddl = tpch_setup.get_tables_with_ddl(schema_name=SCHEMA)
        tpch_ddl = [t for t in tables_ddl if t["table_name"].startswith("TPCH_")]
        assert len(tpch_ddl) >= 5
        for item in tpch_ddl:
            assert "definition" in item
            assert "CREATE TABLE" in item["definition"]

    def test_get_sample_rows(self, tpch_setup):
        samples = tpch_setup.get_sample_rows(schema_name=SCHEMA, tables=["TPCH_REGION"], top_n=3)
        assert len(samples) == 1
        csv_lines = samples[0]["sample_rows"].strip().splitlines()
        assert len(csv_lines) == 4  # header + 3 rows
