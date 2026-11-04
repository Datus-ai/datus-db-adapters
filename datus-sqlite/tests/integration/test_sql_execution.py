from concurrent.futures import ThreadPoolExecutor

import pytest

from datus_sqlite import SQLiteConnector

pytestmark = pytest.mark.integration

EXPECTED = [{"id": 1, "note": None}, {"id": 2, "note": ""}, {"id": 3, "note": "O'Brien"}, {"id": 4, "note": "数据"}]


def test_result_formats_and_empty_result_columns(orders):
    sql = "SELECT * FROM orders ORDER BY id"
    assert orders.execute_query(sql, result_format="list").sql_return == EXPECTED
    assert orders.execute_query(sql, result_format="arrow").sql_return.to_pylist() == EXPECTED
    assert orders.execute_query(sql, result_format="pandas").sql_return.to_dict(orient="records") == EXPECTED
    assert orders.execute_query(sql, result_format="csv").sql_return == "id,note\n1,\n2,\n3,O'Brien\n4,数据\n"
    assert orders.execute_query(
        "SELECT * FROM orders WHERE 1=0", result_format="pandas"
    ).sql_return.columns.tolist() == ["id", "note"]


def test_concurrent_queries_share_connection_safely(orders):
    def query(_):
        result = orders.execute_query("SELECT * FROM orders ORDER BY id", result_format="list")
        assert result.success is True
        return result.sql_return

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(query, range(12))) == [EXPECTED] * 12


def test_dml_accepts_base_context_keywords_and_preserves_cjk(orders):
    context = {"catalog_name": "", "database_name": "main", "schema_name": ""}
    assert orders.execute_insert("INSERT INTO orders VALUES (5, '中文')", **context).success
    assert orders.execute_update("UPDATE orders SET note='测试' WHERE id=5", **context).success
    assert orders.execute_query("SELECT note FROM orders WHERE id=5", result_format="list").sql_return == [
        {"note": "测试"}
    ]
    assert orders.execute_delete("DELETE FROM orders WHERE id=5", **context).success
    assert orders.execute_query("SELECT * FROM orders ORDER BY id", result_format="list").sql_return == EXPECTED


@pytest.mark.parametrize(
    "method, sql",
    [
        ("execute_insert", "INSERT INTO orders VALUES (1, 'duplicate')"),
        ("execute_update", "UPDATE orders SET id=1 WHERE id=2"),
        ("execute_delete", "DELETE FROM orders WHERE id=1"),
        ("execute_ddl", "CREATE TABLE orders (id INTEGER)"),
    ],
)
def test_failed_write_releases_transaction_and_file_lock(orders, method, sql):
    assert orders.execute_ddl(
        "CREATE TRIGGER protect_orders BEFORE DELETE ON orders BEGIN SELECT RAISE(ABORT, 'protected'); END"
    ).success
    # DDL itself need not start an implicit transaction. Verify that a failing
    # DDL also cleans up an already-open transaction.
    orders.connection.execute("BEGIN IMMEDIATE")
    result = getattr(orders, method)(sql)
    assert result.success is False
    assert result.error
    assert orders.connection.in_transaction is False
    with SQLiteConnector({"uri": orders.db_path, "timeout_seconds": 1}) as other:
        assert other.execute_insert("INSERT INTO orders VALUES (5, 'next writer')").success
    assert orders.execute_query("SELECT * FROM orders ORDER BY id", result_format="list").sql_return == [
        *EXPECTED,
        {"id": 5, "note": "next writer"},
    ]


def test_bad_sql_returns_a_useful_error(orders):
    result = orders.execute_query("SELECT FROM", result_format="list")
    assert result.success is False
    assert "syntax" in result.error.lower()
