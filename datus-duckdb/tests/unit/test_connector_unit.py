import sys
from unittest.mock import Mock

from datus_duckdb import DuckDBConfig, DuckdbConnector


def test_file_context_and_close_on_driver_error():
    connector = DuckdbConnector(DuckDBConfig(uri="duckdb:////tmp/retail.duckdb"))
    assert connector.db_path == "/tmp/retail.duckdb"
    assert connector.database_name == "retail"
    assert connector.get_type() == "duckdb"
    assert connector.to_dict() == {"db_type": "duckdb", "db_path": "/tmp/retail.duckdb"}
    driver = Mock()
    driver.close.side_effect = RuntimeError("driver closed")
    connector.connection = driver
    connector.close()
    driver.close.assert_called_once_with()
    assert connector.connection is None


def test_exclusive_connection_reuses_driver(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb_engine", None)
    driver = Mock()
    monkeypatch.setattr("datus_duckdb.connector.duckdb.connect", lambda *args, **kwargs: driver)
    connector = DuckdbConnector({"uri": ":memory:"})
    try:
        with connector.exclusive_connection() as outer:
            with connector.exclusive_connection() as inner:
                assert outer is driver
                assert inner is driver
    finally:
        connector.close()
