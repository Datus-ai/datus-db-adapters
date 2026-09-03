# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import pytest

from datus_dws import DWSConnector
from datus_dws.tpch_data import ROW_COUNTS, TPCH_TABLES


def _query(connector: DWSConnector, sql: str):
    result = connector.execute({"sql_query": sql}, result_format="list")
    assert result.success, result.error
    return result.sql_return


@pytest.mark.integration
@pytest.mark.acceptance
@pytest.mark.parametrize("table_name,expected", list(zip(TPCH_TABLES, ROW_COUNTS)))
def test_tpch_counts(tpch_setup: DWSConnector, table_name, expected):
    schema = tpch_setup.schema_name
    rows = _query(tpch_setup, f'SELECT COUNT(*) AS count FROM "{schema}"."{table_name}"')
    assert rows == [{"count": expected}]


@pytest.mark.integration
def test_tpch_join_and_aggregate(tpch_setup: DWSConnector):
    schema = tpch_setup.schema_name
    rows = _query(
        tpch_setup,
        f"""
        SELECT
            c.name,
            COUNT(*) AS order_count,
            SUM(o.totalprice) AS total_price
        FROM "{schema}"."tpch_customer" c
        JOIN "{schema}"."tpch_orders" o
          ON c.custkey = o.custkey
        GROUP BY c.name
        ORDER BY c.name
        """,
    )

    assert [row["name"] for row in rows] == [f"Customer#{i:03d}" for i in range(1, 11)]
    assert [row["order_count"] for row in rows] == [3, 2, 2, 2, 1, 1, 1, 1, 1, 1]
    assert sum(row["order_count"] for row in rows) == 15


@pytest.mark.integration
def test_tpch_three_table_join(tpch_setup: DWSConnector):
    schema = tpch_setup.schema_name
    rows = _query(
        tpch_setup,
        f"""
        SELECT r.name, COUNT(*) AS supplier_count
        FROM "{schema}"."tpch_supplier" s
        JOIN "{schema}"."tpch_nation" n
          ON s.nationkey = n.nationkey
        JOIN "{schema}"."tpch_region" r
          ON n.regionkey = r.regionkey
        GROUP BY r.name
        ORDER BY r.name
        """,
    )

    assert rows == [
        {"name": "AFRICA", "supplier_count": 1},
        {"name": "AMERICA", "supplier_count": 2},
        {"name": "ASIA", "supplier_count": 2},
    ]


@pytest.mark.integration
def test_tpch_metadata_keeps_dws_distribution(tpch_setup: DWSConnector):
    schema = tpch_setup.schema_name
    tables = tpch_setup.get_tables(schema_name=schema)
    assert {name.rsplit(".", 1)[-1] for name in tables} >= set(TPCH_TABLES)

    ddl_items = tpch_setup.get_tables_with_ddl(schema_name=schema)
    tpch_ddls = [item for item in ddl_items if item["table_name"].startswith("tpch_")]
    assert len(tpch_ddls) == 5
    for item in tpch_ddls:
        ddl = item["definition"]
        assert "orientation=row" in ddl
        assert "DISTRIBUTE BY HASH" in ddl.upper()
