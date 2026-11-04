import sys
from unittest.mock import Mock

import duckdb
import pytest

from datus_db_core import DatusDbException, ErrorCode
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


@pytest.mark.parametrize("failure_prefix", ["SET memory_limit", "SET extension_directory", "ATTACH"])
@pytest.mark.parametrize("close_fails", [False, True])
def test_failed_setup_discards_connection_and_retries_every_setting(monkeypatch, failure_prefix, close_fails):
    monkeypatch.setitem(sys.modules, "duckdb_engine", None)
    monkeypatch.setenv("DATUS_DUCKDB_EXTENSION_DIRECTORY", "/extensions")
    first, second = Mock(), Mock()
    if close_fails:
        first.close.side_effect = RuntimeError("cleanup failed")

    def execute(sql):
        if sql.startswith(failure_prefix):
            raise RuntimeError("injected setup failure")
        return first

    first.execute.side_effect = execute
    connect = Mock(side_effect=[first, second])
    monkeypatch.setattr("datus_duckdb.connector.duckdb.connect", connect)
    db = DuckdbConnector(
        {
            "uri": ":memory:",
            "memory_limit": "256MB",
            "enable_external_access": False,
            "iceberg": {"catalog_uri": "https://catalog.example", "warehouse": "test"},
        }
    )
    try:
        with pytest.raises(DatusDbException, match="injected setup failure") as caught:
            db.connect()
        assert caught.value.code is ErrorCode.DB_CONNECTION_FAILED
        assert str(caught.value.__cause__) == "injected setup failure"
        first.close.assert_called_once_with()
        assert db.connection is None
        db.connect()
        assert db.connection is second
        assert connect.call_count == 2
        sqls = [c.args[0] for c in second.execute.call_args_list]
        assert sqls[:3] == [
            "SET extension_directory='/extensions'",
            "SET memory_limit='256MB'",
            "SET enable_external_access=false",
        ]
        assert sqls[-1].startswith("ATTACH 'test'")
    finally:
        db.close()
    second.close.assert_called_once_with()


@pytest.mark.parametrize(
    "endpoint, configured, expected",
    [
        (None, None, True),
        ("s3.example.com", None, True),
        ("minio.internal:9000", None, True),
        ("https://s3.example.com", None, True),
        ("http://minio.internal:9000", None, False),
        ("https://s3.example.com", False, False),
        ("http://minio", True, True),
        ("s3.example.com", "false", False),
        ("s3.example.com", "true", True),
    ],
)
def test_s3_transport_defaults_and_explicit_overrides(endpoint, configured, expected):
    assert DuckdbConnector._s3_use_ssl({"s3_use_ssl": configured}, endpoint) is expected


def test_get_schema_retries_catalog_error_without_database(monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb_engine", None)
    monkeypatch.delenv("DATUS_DUCKDB_EXTENSION_DIRECTORY", raising=False)
    driver, result = Mock(), Mock()
    result.description = [(c,) for c in ("cid", "name", "type", "notnull", "dflt_value", "pk")]
    result.fetchall.return_value = [(0, "id", "INTEGER", True, "7", True), (1, "note", "VARCHAR", False, None, False)]
    driver.execute.side_effect = [duckdb.CatalogException("missing catalog"), result]
    monkeypatch.setattr("datus_duckdb.connector.duckdb.connect", lambda *a, **k: driver)
    db = DuckdbConnector({"uri": ":memory:", "database_name": "retail"})
    try:
        assert db.get_schema(table_name="orders") == [
            {"cid": 0, "name": "id", "type": "INTEGER", "nullable": False, "default_value": "7", "pk": True},
            {"cid": 1, "name": "note", "type": "VARCHAR", "nullable": True, "default_value": None, "pk": False},
        ]
        assert [c.args[0] for c in driver.execute.call_args_list] == [
            'PRAGMA table_info(\'"retail"."main"."orders"\')',
            'PRAGMA table_info(\'"main"."orders"\')',
        ]
    finally:
        db.close()


@pytest.mark.parametrize("database_name, attempts", [("", 1), ("retail", 2)])
def test_get_schema_maps_unrecoverable_catalog_errors(monkeypatch, database_name, attempts):
    monkeypatch.setitem(sys.modules, "duckdb_engine", None)
    monkeypatch.delenv("DATUS_DUCKDB_EXTENSION_DIRECTORY", raising=False)
    driver = Mock()
    driver.execute.side_effect = lambda *a: (_ for _ in ()).throw(duckdb.CatalogException("missing table"))
    monkeypatch.setattr("datus_duckdb.connector.duckdb.connect", lambda *a, **k: driver)
    db = DuckdbConnector({"uri": ":memory:", "database_name": database_name})
    try:
        with pytest.raises(DatusDbException, match="missing table") as caught:
            db.get_schema(table_name="orders")
        assert caught.value.code is ErrorCode.DB_QUERY_METADATA_FAILED
        assert driver.execute.call_count == attempts
    finally:
        db.close()
