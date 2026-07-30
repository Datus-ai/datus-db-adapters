# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConnector


@pytest.mark.integration
def test_tpch_metadata(tpch_setup: DorisConnector):
    database = tpch_setup.doris_config.database
    expected_tables = {
        f"{database}.tpch_region",
        f"{database}.tpch_nation",
        f"{database}.tpch_customer",
        f"{database}.tpch_orders",
        f"{database}.tpch_supplier",
    }
    assert expected_tables.issubset(set(tpch_setup.get_tables()))

    expected_columns = {
        "tpch_customer": {"custkey", "name", "nationkey"},
        "tpch_nation": {"nationkey", "name", "regionkey"},
    }
    for table, expected in expected_columns.items():
        columns = tpch_setup.get_schema(table_name=table)
        assert expected.issubset({column["name"] for column in columns})
        assert all("type" in column for column in columns)


@pytest.mark.integration
@pytest.mark.acceptance
@pytest.mark.parametrize(
    ("table", "expected_rows"),
    [("tpch_region", 5), ("tpch_nation", 25)],
)
def test_tpch_row_counts(
    tpch_setup: DorisConnector,
    table: str,
    expected_rows: int,
):
    result = tpch_setup.execute(
        {"sql_query": f"SELECT * FROM `{table}`"},
        result_format="list",
    )
    assert result.success
    assert len(result.sql_return) == expected_rows


@pytest.mark.integration
def test_tpch_join(tpch_setup: DorisConnector):
    result = tpch_setup.execute(
        {
            "sql_query": (
                "SELECT n.name AS nation_name, r.name AS region_name "
                "FROM `tpch_nation` n "
                "JOIN `tpch_region` r ON n.regionkey = r.regionkey "
                "ORDER BY n.nationkey"
            )
        },
        result_format="list",
    )

    assert result.success
    assert len(result.sql_return) == 25
    assert result.sql_return[0] == {
        "nation_name": "ALGERIA",
        "region_name": "AFRICA",
    }


@pytest.mark.integration
def test_tpch_aggregation(tpch_setup: DorisConnector):
    result = tpch_setup.execute(
        {
            "sql_query": (
                "SELECT r.name AS region_name, "
                "COUNT(n.nationkey) AS nation_count "
                "FROM `tpch_region` r "
                "JOIN `tpch_nation` n ON r.regionkey = n.regionkey "
                "GROUP BY r.name "
                "ORDER BY r.name"
            )
        },
        result_format="list",
    )

    assert result.success
    assert len(result.sql_return) == 5
    assert sum(row["nation_count"] for row in result.sql_return) == 25


@pytest.mark.integration
def test_tpch_customer_orders(tpch_setup: DorisConnector):
    result = tpch_setup.execute(
        {
            "sql_query": (
                "SELECT c.name, COUNT(o.orderkey) AS order_count, "
                "SUM(o.totalprice) AS total_spent "
                "FROM `tpch_customer` c "
                "JOIN `tpch_orders` o ON c.custkey = o.custkey "
                "GROUP BY c.name "
                "ORDER BY order_count DESC "
                "LIMIT 5"
            )
        },
        result_format="list",
    )

    assert result.success
    assert result.sql_return
    assert {"order_count", "total_spent"}.issubset(result.sql_return[0])


@pytest.mark.integration
@pytest.mark.parametrize("result_format", ["csv", "arrow", "pandas"])
def test_tpch_result_formats(
    tpch_setup: DorisConnector,
    result_format: str,
):
    result = tpch_setup.execute(
        {"sql_query": ("SELECT regionkey, name FROM `tpch_region` ORDER BY regionkey")},
        result_format=result_format,
    )

    assert result.success
    if result_format == "csv":
        assert "AFRICA" in result.sql_return
        assert "ASIA" in result.sql_return
    elif result_format == "arrow":
        assert result.sql_return.num_rows == 5
        assert result.sql_return.column_names == ["regionkey", "name"]
    else:
        assert len(result.sql_return) == 5
        assert list(result.sql_return.columns) == ["regionkey", "name"]
