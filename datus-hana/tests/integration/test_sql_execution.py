from datetime import date
from decimal import Decimal

import pytest

from datus_db_core import DatusDbException
from datus_hana.analysis import analyze_hana_sql

pytestmark = pytest.mark.integration


def test_native_scalar_analysis_and_sdk_execution_agree(connector):
    sql = """SELECT TO_VARCHAR(DATE '2026-10-01', 'YYYY-MM') AS "month_key",
        ADD_DAYS(DATE '2026-10-01', 1) AS "next_day",
        DAYS_BETWEEN(DATE '2026-10-01', DATE '2026-10-03') AS "days",
        SECONDS_BETWEEN(TIMESTAMP '2026-10-01 00:00:00', TIMESTAMP '2026-10-02 00:00:00') AS "seconds",
        TO_DECIMAL('1.25') AS "decimal_value",
        TO_BIGINT('9007199254740993') AS "integer_value",
        WEEKDAY(DATE '2026-10-01') AS "weekday",
        ADD_YEARS(DATE '2026-10-01', 1) AS "next_year",
        HOUR(TIMESTAMP '2026-10-01 03:04:05') AS "hour",
        MINUTE(TIMESTAMP '2026-10-01 03:04:05') AS "minute"
        FROM DUMMY"""
    assert analyze_hana_sql(sql).require_complete().tables == ('"DUMMY"',)
    result = connector.execute_query(sql, "list")
    assert result.success, result.error
    assert result.sql_return == [
        {
            "month_key": "2026-10",
            "next_day": date(2026, 10, 2),
            "days": 2,
            "seconds": 86400,
            "decimal_value": Decimal("1.25"),
            "integer_value": 9007199254740993,
            "weekday": 3,
            "next_year": date(2027, 10, 1),
            "hour": 3,
            "minute": 4,
        }
    ]


@pytest.mark.parametrize("result_format", ["list", "arrow"])
@pytest.mark.parametrize(
    "low,high,mean",
    [
        ("9007199254740992.00", "9007199254740992.02", "9007199254740992.01"),
        (
            "12345678901234567890123456789012.10",
            "12345678901234567890123456789012.12",
            "12345678901234567890123456789012.11",
        ),
    ],
)
def test_decimal_average_preserves_low_order_digits(connector, result_format, low, high, mean):
    sql = f"""SELECT AVG(TO_DECIMAL(amount)) AS "mean" FROM (
        SELECT TO_DECIMAL('{low}', 38, 2) AS amount FROM DUMMY
        UNION ALL SELECT TO_DECIMAL('{high}', 38, 2) FROM DUMMY
    ) samples"""
    assert analyze_hana_sql(sql).require_complete().tables == ('"DUMMY"',)
    result = connector.execute_query(sql, result_format)
    assert result.success, result.error
    rows = result.sql_return if result_format == "list" else result.sql_return.to_pylist()
    assert rows == [{"mean": Decimal(mean)}]


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
