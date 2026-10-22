import time
from unittest.mock import patch

import pymysql
import pytest
from pydantic import ValidationError
from sqlalchemy import event

from datus_db_core import DatusDbException, ErrorCode
from datus_mysql.testing import StallingMySQLServer
from datus_starrocks import StarRocksConfig, StarRocksConnector


@pytest.mark.parametrize(
    "config, expected",
    [
        ({}, (30, 30, 30)),
        ({"timeout_seconds": 7}, (7, 7, 7)),
        (
            {"timeout_seconds": 7, "connect_timeout_seconds": 2, "read_timeout_seconds": 3, "write_timeout_seconds": 4},
            (2, 3, 4),
        ),
    ],
)
def test_timeout_arguments_reach_real_driver(config, expected):
    connector = StarRocksConnector(StarRocksConfig(username="test", **config))
    try:
        engine = connector._ensure_engine()
        with patch.object(pymysql, "connect", side_effect=RuntimeError("DBAPI boundary")) as connect:
            with pytest.raises(RuntimeError, match="DBAPI boundary"):
                engine.pool._creator()
        args = connect.call_args.kwargs
        assert tuple(args.get(key) for key in ("connect_timeout", "read_timeout", "write_timeout")) == expected
    finally:
        connector.close()


@pytest.mark.parametrize(
    "field",
    [
        "timeout_seconds",
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "write_timeout_seconds",
        "query_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, "invalid"])
def test_timeouts_must_be_positive(field, value):
    with pytest.raises(ValidationError) as exc:
        StarRocksConfig(username="test", **{field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


def test_server_query_deadline_is_installed_on_replacement_connections():
    with StallingMySQLServer(lambda sql: False) as server:
        connector = StarRocksConnector({"username": "test", "port": server.port, "query_timeout_seconds": 2})
        try:
            # Query success proves the real DBAPI cursor accepted session setup.
            assert connector.execute_query("SELECT 42").success is True
            connector.close()
            assert connector.execute_query("SELECT 42").success is True
            assert server.connections == 2
            assert server.statements.count("SET query_timeout = 2") == 2
        finally:
            connector.close()


@pytest.mark.parametrize("catalog", ["default_catalog", "external"])
@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_stalled_response_closes_connection_without_replaying(operation, catalog):
    target = {
        "query": "SELECT issue_165",
        "metadata": "information_schema.TABLES",
        "test_connection": "SELECT 1",
    }[operation]
    with StallingMySQLServer(lambda sql: target in sql) as server:
        connector = StarRocksConnector(
            {"username": "test", "port": server.port, "timeout_seconds": 1, "catalog": catalog, "database": "app"}
        )
        engine = connector._ensure_engine()
        invalidated = []
        event.listen(engine, "invalidate", lambda *args: invalidated.append(args))
        try:
            started = time.monotonic()
            if operation == "query":
                result = connector.execute_query(target)
                assert result.success is False
                assert "timed out" in result.error
            else:
                with pytest.raises(DatusDbException):
                    if operation == "metadata":
                        connector.get_tables()
                    else:
                        connector.test_connection()
            elapsed = time.monotonic() - started
            assert server.stalled.is_set(), server.statements
            assert elapsed < 3, f"Socket read was not bounded: {elapsed:.2f}s"
            assert server.client_closed.wait(1)
            assert len(invalidated) == 1
            assert server.connections == 1
            assert sum(target in sql for sql in server.statements) == 1
            # An explicit later operation uses a fresh connection and succeeds.
            assert connector.execute_query("SELECT 42").success is True
            assert server.connections == 2
        finally:
            connector.close()


def test_unsupported_server_deadline_fails_before_application_sql():
    with StallingMySQLServer(
        lambda sql: False, reject=lambda sql: "max_execution_time" in sql or "query_timeout" in sql
    ) as server:
        connector = StarRocksConnector({"username": "test", "port": server.port, "query_timeout_seconds": 2})
        try:
            result = connector.execute_query("SELECT 42")
            assert result.success is False
            assert "Unknown system variable" in result.error
            assert "SELECT 42" not in server.statements
            assert server.connections == 1
        finally:
            connector.close()


def test_stalled_handshake_is_bounded():
    with StallingMySQLServer(lambda sql: False, stall_handshake=True) as server:
        connector = StarRocksConnector({"username": "test", "port": server.port, "timeout_seconds": 1})
        try:
            started = time.monotonic()
            with pytest.raises(DatusDbException) as exc:
                connector.test_connection()
            assert exc.value.code == ErrorCode.DB_CONNECTION_TIMEOUT
            assert time.monotonic() - started < 3
            assert server.stalled.is_set()
            assert server.client_closed.wait(1)
            assert server.connections == 1
            assert server.statements == []
        finally:
            connector.close()
