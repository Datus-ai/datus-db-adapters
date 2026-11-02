from concurrent.futures import ThreadPoolExecutor

import pytest

from datus_sqlite import SQLiteConnector

pytestmark = pytest.mark.integration


def test_file_roundtrip_formats_and_read_only(tmp_path):
    path = str(tmp_path / "native.db")
    db = SQLiteConnector({"uri": path})
    try:
        assert db.execute_ddl("CREATE TABLE orders (id INTEGER, note TEXT)").success
        assert db.execute_insert("INSERT INTO orders VALUES (1, NULL), (2, ''), (3, 'O''Brien')").success
        expected = [{"id": 1, "note": None}, {"id": 2, "note": ""}, {"id": 3, "note": "O'Brien"}]

        def query(_):
            return db.execute_query("SELECT * FROM orders ORDER BY id", result_format="list").sql_return

        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(query, range(12))) == [expected] * 12
        assert db.get_tables() == ["orders"]
        assert (
            db.execute_query("SELECT * FROM orders ORDER BY id", result_format="arrow").sql_return.to_pylist()
            == expected
        )
        assert db.execute_query(
            "SELECT * FROM orders WHERE 1=0", result_format="pandas"
        ).sql_return.columns.tolist() == ["id", "note"]
    finally:
        db.close()
    ro = SQLiteConnector({"uri": path, "read_only": True})
    try:
        assert ro.test_connection() is True
        assert ro.execute_query("SELECT COUNT(*) AS n FROM orders", result_format="list").sql_return == [{"n": 3}]
        result = ro.execute_insert("INSERT INTO orders VALUES (4, 'forbidden')")
        assert result.success is False
        assert "readonly" in result.error.lower() or "read-only" in result.error.lower()
    finally:
        ro.close()
