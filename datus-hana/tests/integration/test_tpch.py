import pytest

from datus_hana.tpch_data import ROW_COUNTS, TPCH_TABLES

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("table,count", list(zip(TPCH_TABLES, ROW_COUNTS, strict=True)))
def test_tpch_counts(connector, tpch_setup, table, count):
    assert table in connector.get_tables()
    result = connector.execute_query(f'SELECT COUNT(*) AS "n" FROM "{table}"', "list")
    assert result.success and result.sql_return == [{"n": count}]


@pytest.mark.parametrize("fmt", ["csv", "arrow", "pandas", "list"])
def test_join_aggregate_formats(connector, tpch_setup, fmt):
    sql = 'SELECT r.NAME AS "region", COUNT(*) AS "n" FROM "tpch_region" r JOIN "tpch_nation" n ON r.REGIONKEY = n.REGIONKEY GROUP BY r.NAME ORDER BY r.NAME'
    result = connector.execute_query(sql, fmt)
    assert result.success, result.error
    assert result.row_count == 5
    rows = result.sql_return
    if fmt == "csv":
        assert "AFRICA,5" in rows
    else:
        rows = rows if fmt == "list" else rows.to_pylist() if fmt == "arrow" else rows.to_dict("records")
        assert rows[0] == {"region": "AFRICA", "n": 5}
