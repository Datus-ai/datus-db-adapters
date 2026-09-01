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

from datus_oracle.tpch_data import ROW_COUNTS, TPCH_TABLES

pytestmark = pytest.mark.integration

SCHEMA = os.getenv("ORACLE_SCHEMA", "DATUS_TEST")


class TestTpchDataValidation:
    """Validate that TPC-H sample data was loaded correctly."""

    @pytest.mark.parametrize(
        "table_name,expected",
        [(table.upper(), count) for table, count in zip(TPCH_TABLES, ROW_COUNTS)],
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
                    SELECT r."NAME" AS region, COUNT(*) AS nation_count
                    FROM "{SCHEMA}"."TPCH_REGION" r
                    JOIN "{SCHEMA}"."TPCH_NATION" n ON r."REGIONKEY" = n."REGIONKEY"
                    GROUP BY r."NAME"
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
                    SELECT c."NAME" AS c_name,
                           COUNT(o."ORDERKEY") AS order_count,
                           SUM(o."TOTALPRICE") AS total_spent
                    FROM "{SCHEMA}"."TPCH_CUSTOMER" c
                    JOIN "{SCHEMA}"."TPCH_ORDERS" o ON c."CUSTKEY" = o."CUSTKEY"
                    GROUP BY c."NAME"
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
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT s."NAME" AS s_name, n."NAME" AS nation, r."NAME" AS region
                    FROM "{SCHEMA}"."TPCH_SUPPLIER" s
                    JOIN "{SCHEMA}"."TPCH_NATION" n ON s."NATIONKEY" = n."NATIONKEY"
                    JOIN "{SCHEMA}"."TPCH_REGION" r ON n."REGIONKEY" = r."REGIONKEY"
                    ORDER BY s."SUPPKEY"
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

    def test_fetch_first_top_n(self, tpch_setup):
        """FETCH FIRST replaces LIMIT for top-N queries."""
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT "ORDERKEY" AS o_orderkey, "TOTALPRICE" AS o_totalprice
                    FROM "{SCHEMA}"."TPCH_ORDERS"
                    ORDER BY "TOTALPRICE" DESC
                    FETCH FIRST 3 ROWS ONLY
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert len(result.sql_return) == 3
        prices = [float(row["o_totalprice"]) for row in result.sql_return]
        assert prices == sorted(prices, reverse=True)

    def test_date_literals_round_trip(self, tpch_setup):
        """Rows load with explicit DATE literals, so ORDERDATE is a real DATE.

        Non-obvious: without the ``DATE ''`` prefix the load would depend on the
        session's NLS_DATE_FORMAT, which differs between Oracle images.
        """
        result = tpch_setup.execute(
            {
                "sql_query": f"""
                    SELECT COUNT(*) AS cnt
                    FROM "{SCHEMA}"."TPCH_ORDERS"
                    WHERE "ORDERDATE" BETWEEN DATE '1992-01-01' AND DATE '1996-12-31'
                """
            },
            result_format="list",
        )
        assert result.success, result.error
        assert result.sql_return[0]["cnt"] == 15


class TestTpchMetadata:
    """Validate metadata retrieval for TPC-H tables."""

    def test_get_tables_includes_tpch(self, tpch_setup):
        tables = tpch_setup.get_tables(schema_name=SCHEMA)
        expected = {table.upper() for table in TPCH_TABLES}
        assert expected.issubset(set(tables))

    def test_get_schema_columns(self, tpch_setup):
        cols = tpch_setup.get_schema(schema_name=SCHEMA, table_name="TPCH_REGION")
        col_names = [c["name"] for c in cols]
        assert col_names == ["REGIONKEY", "NAME", "COMMENT"]
        pk_col = [c for c in cols if c["name"] == "REGIONKEY"][0]
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
