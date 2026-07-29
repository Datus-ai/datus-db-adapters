# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import pytest

from datus_hologres import HologresConnector


def _query(connector: HologresConnector, sql: str):
    result = connector.execute({"sql_query": sql}, result_format="list")
    assert result.success, result.error
    return result.sql_return


@pytest.mark.integration
@pytest.mark.acceptance
def test_tiny_tpch_counts(tpch_setup: HologresConnector):
    schema = tpch_setup.schema_name
    expected = {
        "tpch_region": 5,
        "tpch_nation": 5,
        "tpch_supplier": 3,
        "tpch_customer": 4,
        "tpch_orders": 6,
    }

    for table_name, count in expected.items():
        rows = _query(tpch_setup, f'SELECT COUNT(*) AS count FROM "{schema}"."{table_name}"')
        assert rows == [{"count": count}]


@pytest.mark.integration
def test_tiny_tpch_join_and_aggregate(tpch_setup: HologresConnector):
    schema = tpch_setup.schema_name
    rows = _query(
        tpch_setup,
        f"""
        SELECT
            c.c_name,
            COUNT(*) AS order_count,
            SUM(o.o_totalprice) AS total_price
        FROM "{schema}"."tpch_customer" c
        JOIN "{schema}"."tpch_orders" o
          ON c.c_custkey = o.o_custkey
        GROUP BY c.c_name
        ORDER BY c.c_name
        """,
    )

    assert [row["c_name"] for row in rows] == ["Customer#1", "Customer#2", "Customer#3", "Customer#4"]
    assert [row["order_count"] for row in rows] == [2, 1, 2, 1]


@pytest.mark.integration
def test_tiny_tpch_three_table_join(tpch_setup: HologresConnector):
    schema = tpch_setup.schema_name
    rows = _query(
        tpch_setup,
        f"""
        SELECT r.r_name, COUNT(*) AS supplier_count
        FROM "{schema}"."tpch_supplier" s
        JOIN "{schema}"."tpch_nation" n
          ON s.s_nationkey = n.n_nationkey
        JOIN "{schema}"."tpch_region" r
          ON n.n_regionkey = r.r_regionkey
        GROUP BY r.r_name
        ORDER BY r.r_name
        """,
    )

    assert rows == [
        {"r_name": "AMERICA", "supplier_count": 1},
        {"r_name": "ASIA", "supplier_count": 1},
        {"r_name": "EUROPE", "supplier_count": 1},
    ]


@pytest.mark.integration
def test_tiny_tpch_metadata(tpch_setup: HologresConnector):
    schema = tpch_setup.schema_name
    tables = tpch_setup.get_tables(schema_name=schema)
    assert {name.rsplit(".", 1)[-1] for name in tables} >= {
        "tpch_region",
        "tpch_nation",
        "tpch_supplier",
        "tpch_customer",
        "tpch_orders",
    }

    ddl_items = tpch_setup.get_tables_with_ddl(schema_name=schema)
    tpch_ddls = [item for item in ddl_items if item["table_name"].startswith("tpch_")]
    assert len(tpch_ddls) == 5
    assert all("orientation = 'column'" in item["definition"] for item in tpch_ddls)
