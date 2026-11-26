from unittest.mock import patch

import pytest

from datus_clickhouse import ClickHouseConnector


def test_timeout_reaches_actual_driver():
    connector = ClickHouseConnector({"username": "test", "timeout_seconds": 7})
    try:
        engine = connector._ensure_engine()
        with patch(
            "clickhouse_sqlalchemy.drivers.http.connector.connect", side_effect=RuntimeError("DBAPI boundary")
        ) as connect:
            with pytest.raises(RuntimeError, match="DBAPI boundary"):
                engine.pool._creator()
        for key, value in {"timeout": 7, "ch_settings": {"max_execution_time": 7}}.items():
            assert connect.call_args.kwargs.get(key) == value
    finally:
        connector.close()


@pytest.mark.parametrize("field", ["timeout_seconds", "query_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_clickhouse import ClickHouseConfig

    with pytest.raises(ValidationError):
        ClickHouseConfig(**{"username": "test"}, **{field: value})


def test_query_timeout_override_scope_updates_and_restores_transport_settings():
    from types import SimpleNamespace

    connector = ClickHouseConnector({"username": "test", "timeout_seconds": 15, "query_timeout_seconds": 4})
    try:
        assert connector.supports_query_timeout_override is True
        original = {"max_execution_time": 4, "readonly": 1}
        transport = SimpleNamespace(ch_settings=original)
        conn = SimpleNamespace(connection=SimpleNamespace(driver_connection=SimpleNamespace(transport=transport)))

        with connector._query_timeout_scope(conn, 9):
            assert transport.ch_settings == {"max_execution_time": 9, "readonly": 1}
            assert original == {"max_execution_time": 4, "readonly": 1}
        assert transport.ch_settings is original

        with pytest.raises(RuntimeError, match="query failed"):
            with connector._query_timeout_scope(conn, 3):
                assert transport.ch_settings["max_execution_time"] == 3
                raise RuntimeError("query failed")
        assert transport.ch_settings is original
    finally:
        connector.close()
