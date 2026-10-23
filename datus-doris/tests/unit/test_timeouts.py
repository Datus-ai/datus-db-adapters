from unittest.mock import patch

import pymysql
import pytest
from pydantic import ValidationError

from datus_doris import DorisConfig, DorisConnector
from datus_mysql.testing import StallingMySQLServer


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
    connector = DorisConnector(DorisConfig(username="test", **config))
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
        DorisConfig(username="test", **{field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


def test_server_query_deadline_is_installed_on_replacement_connections():
    with StallingMySQLServer(lambda sql: False) as server:
        connector = DorisConnector({"username": "test", "port": server.port, "query_timeout_seconds": 2})
        try:
            # Query success proves the real DBAPI cursor accepted session setup.
            assert connector.execute_query("SELECT 42").success is True
            connector.close()
            assert connector.execute_query("SELECT 42").success is True
            assert server.connections == 2
            assert server.statements.count("SET query_timeout = 2") == 2
        finally:
            connector.close()


def test_unsupported_server_deadline_fails_before_application_sql():
    with StallingMySQLServer(
        lambda sql: False, reject=lambda sql: "max_execution_time" in sql or "query_timeout" in sql
    ) as server:
        connector = DorisConnector({"username": "test", "port": server.port, "query_timeout_seconds": 2})
        try:
            result = connector.execute_query("SELECT 42")
            assert result.success is False
            assert "Unknown system variable" in result.error
            assert "SELECT 42" not in server.statements
            assert server.connections == 1
        finally:
            connector.close()
