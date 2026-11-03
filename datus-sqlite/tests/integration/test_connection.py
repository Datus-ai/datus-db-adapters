import pytest

from datus_sqlite import SQLiteConfig, SQLiteConnector

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("as_model", [False, True])
def test_connection_context_manager(tmp_path, as_model):
    values = {"uri": str(tmp_path / "context.db")}
    db = SQLiteConnector(SQLiteConfig(**values) if as_model else values)
    with db:
        assert db.test_connection() is True
        assert db.execute_query("SELECT 7 AS value", result_format="list").sql_return == [{"value": 7}]
    assert db.connection is None


def test_read_only_file_retains_rows_and_rejects_writes(orders):
    path = orders.db_path
    orders.close()
    with SQLiteConnector({"uri": path, "read_only": True}) as db:
        assert db.test_connection() is True
        assert db.execute_query("SELECT COUNT(*) AS n FROM orders", result_format="list").sql_return == [{"n": 4}]
        result = db.execute_insert("INSERT INTO orders VALUES (5, 'forbidden')")
        assert result.success is False
        assert "readonly" in result.error.lower() or "read-only" in result.error.lower()
        assert db.execute_query("SELECT COUNT(*) AS n FROM orders", result_format="list").sql_return == [{"n": 4}]
