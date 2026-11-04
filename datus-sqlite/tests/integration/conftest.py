"""Disposable local SQLite databases; no external environment is required."""

import pytest

from datus_sqlite import SQLiteConnector


@pytest.fixture
def connector(tmp_path):
    db = SQLiteConnector({"uri": str(tmp_path / "native.db"), "timeout_seconds": 1})
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def orders(connector):
    assert connector.execute_ddl("CREATE TABLE orders (id INTEGER PRIMARY KEY, note TEXT)").success
    assert connector.execute_insert(
        "INSERT INTO orders VALUES (1, NULL), (2, ''), (3, 'O''Brien'), (4, '数据')"
    ).success
    return connector
