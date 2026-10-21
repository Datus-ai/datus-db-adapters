from datetime import date

import pytest

from datus_db_core import DatusDbException
from datus_hana.analysis import analyze_hana_sql

pytestmark = pytest.mark.integration


def test_native_scalar_analysis_and_sdk_execution_agree(connector):
    sql = """SELECT TO_VARCHAR(DATE '2026-10-01', 'YYYY-MM') AS "month_key",
        ADD_DAYS(DATE '2026-10-01', 1) AS "next_day",
        DAYS_BETWEEN(DATE '2026-10-01', DATE '2026-10-03') AS "days",
        SECONDS_BETWEEN(TIMESTAMP '2026-10-01 00:00:00', TIMESTAMP '2026-10-02 00:00:00') AS "seconds"
        FROM DUMMY"""
    assert analyze_hana_sql(sql).require_complete().tables == ('"DUMMY"',)
    result = connector.execute_query(sql, "list")
    assert result.success, result.error
    assert result.sql_return == [{"month_key": "2026-10", "next_day": date(2026, 10, 2), "days": 2, "seconds": 86400}]


def test_bound_unicode_crud_and_transactions(connector, metadata_objects_setup):
    table = metadata_objects_setup["table_ref"]
    result = connector.execute_query(f"INSERT INTO {table} VALUES (?, ?)", parameters=(2, "数据"))
    assert result.success and result.row_count == 1
    result = connector.execute_update(f'UPDATE {table} SET "text" = \'更新\' WHERE "Mixed Column" = 2')
    assert result.success and result.row_count == 1
    result = connector.execute_query(f'SELECT "text" FROM {table} WHERE "Mixed Column" = ?', "list", parameters=(2,))
    assert result.success and result.sql_return == [{"text": "更新"}]
    result = connector.execute_delete(f'DELETE FROM {table} WHERE "Mixed Column" = 2')
    assert result.success and result.row_count == 1
    with pytest.raises(DatusDbException):
        connector.execute_queries(
            [f"INSERT INTO {table} VALUES (3, 'rollback')", "INSERT INTO no_such_hana_table VALUES (1)"]
        )
    result = connector.execute_query(f'SELECT COUNT(*) AS "n" FROM {table} WHERE "Mixed Column" = 3', "list")
    assert result.success and result.sql_return == [{"n": 0}]


def test_invalid_sql_is_error_result(connector):
    result = connector.execute_query("SELECT * FROM", "list")
    assert not result.success and result.error
