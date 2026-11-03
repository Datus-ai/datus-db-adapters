import sqlite3
from unittest.mock import Mock

import pytest

from datus_sqlite import SQLiteConfig, SQLiteConnector


def test_context_quoting_and_close_failure():
    db = SQLiteConnector(SQLiteConfig(uri="sqlite:////tmp/retail.db"))
    assert db.db_path == "/tmp/retail.db"
    assert db.database_name == "retail"
    assert db.get_type() == "sqlite"
    assert db.to_dict() == {"db_type": "sqlite", "db_path": "/tmp/retail.db"}
    assert db.quote_identifier('my"table') == '"my""table"'
    assert db.full_name(database_name="retail", table_name='my"table') == '"my""table"'
    driver = Mock()
    driver.close.side_effect = RuntimeError("close failed")
    db.connection = driver
    db.close()
    driver.close.assert_called_once_with()
    assert db.connection is None


@pytest.mark.parametrize("method", ["execute_insert", "execute_update", "execute_delete", "execute_ddl"])
@pytest.mark.parametrize("failure_point", ["execute", "commit"])
def test_write_errors_roll_back_without_losing_the_original_error(method, failure_point):
    db = SQLiteConnector({"uri": ":memory:"})
    driver = Mock()
    db.connection = driver

    def fail(*args):
        raise sqlite3.IntegrityError("original write failure")

    def fail_rollback():
        raise RuntimeError("rollback failed")

    if failure_point == "execute":
        driver.cursor.return_value.execute.side_effect = fail
    else:
        driver.commit.side_effect = fail
    driver.rollback.side_effect = fail_rollback
    try:
        result = getattr(db, method)("WRITE TEST")
        assert result.success is False
        assert "original write failure" in result.error
        assert "rollback failed" not in result.error
        assert "500011" in result.error
        driver.rollback.assert_called_once_with()
    finally:
        db.close()


@pytest.mark.parametrize("method", ["execute_insert", "execute_update", "execute_delete", "execute_ddl"])
def test_connect_failure_does_not_require_a_connection_to_roll_back(monkeypatch, method):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("open failed")

    monkeypatch.setattr("datus_sqlite.connector.sqlite3.connect", fail)
    db = SQLiteConnector({"uri": ":memory:"})
    try:
        result = getattr(db, method)("WRITE TEST")
        assert result.success is False
        assert "open failed" in result.error
        assert db.connection is None
    finally:
        db.close()


def test_metadata_binds_table_type_and_limit_values():
    db = SQLiteConnector({"uri": ":memory:"})
    driver = Mock()
    cursor = driver.cursor.return_value
    cursor.fetchall.return_value = []
    db.connection = driver
    try:
        assert db._get_schema_with_ddl(table_type="table' OR 1=1 --") == []
        cursor.execute.assert_called_once_with(
            "SELECT name, sql FROM sqlite_master WHERE type=?", ("table' OR 1=1 --",)
        )
        cursor.execute.reset_mock()
        assert db.get_sample_rows(tables=['my"table'], top_n=2) == []
        cursor.execute.assert_called_once_with('SELECT * FROM "my""table" LIMIT ?', (2,))
    finally:
        db.close()
