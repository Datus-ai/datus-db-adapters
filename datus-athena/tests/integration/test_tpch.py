import csv
import io

import pytest

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("table,count", list(zip(TPCH_TABLES, ROW_COUNTS, strict=True)))
def test_row_counts(connector, table, count):
    result = connector.execute_query(f"SELECT COUNT(*) AS count FROM {connector.full_name(table_name=table)}", "list")
    assert result.success, result.error
    assert result.sql_return == [{"count": count}]
    assert result.execution_info["statistics"]["DataScannedInBytes"] >= 0


@pytest.mark.parametrize("fmt", ["list", "csv", "arrow", "pandas"])
def test_join_aggregation_and_formats(connector, fmt):
    nation = connector.full_name(table_name="tpch_nation")
    region = connector.full_name(table_name="tpch_region")
    sql = (
        f"SELECT COUNT(*) AS count FROM {nation} n JOIN {region} r ON n.n_regionkey=r.r_regionkey WHERE r.r_name='ASIA'"
    )
    result = connector.execute_query(sql, fmt)
    assert result.success, result.error
    if fmt == "list":
        assert result.sql_return == [{"count": 5}]
    elif fmt == "arrow":
        assert result.sql_return.to_pylist() == [{"count": 5}]
    elif fmt == "pandas":
        assert result.sql_return.to_dict("records") == [{"count": 5}]
    else:
        assert list(csv.DictReader(io.StringIO(result.sql_return))) == [{"count": "5"}]
