from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from datus_oracle import OracleConnector


def test_timeout_reaches_actual_driver():
    connector = OracleConnector({"username": "test", "service_name": "FREEPDB1", "timeout_seconds": 7})
    try:
        engine = connector._ensure_engine()
        with patch("oracledb.connect", side_effect=RuntimeError("DBAPI boundary")) as connect:
            with pytest.raises(RuntimeError, match="DBAPI boundary"):
                engine.pool._creator()
        for key, value in {"tcp_connect_timeout": 7}.items():
            assert connect.call_args.kwargs.get(key) == value
    finally:
        connector.close()


@pytest.mark.parametrize("field", ["timeout_seconds", "call_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_oracle import OracleConfig

    with pytest.raises(ValidationError):
        OracleConfig(**{"username": "test", "service_name": "FREEPDB1"}, **{field: value})


@pytest.mark.parametrize("override, milliseconds", [(None, 7000), (3, 3000)])
def test_call_timeout_is_set_before_initialization_and_on_replacements(override, milliseconds):
    connector = OracleConnector(
        {"username": "test", "service_name": "FREEPDB1", "timeout_seconds": 7, "call_timeout_seconds": override}
    )
    try:
        engine = connector._ensure_engine()
        observed = []
        with patch.object(
            engine.dialect,
            "initialize",
            side_effect=lambda conn: observed.append(conn.connection.dbapi_connection.call_timeout),
        ):
            for _ in range(2):
                connection = Mock()
                engine.pool.dispatch.connect(connection, None)
                assert connection.call_timeout == milliseconds
        assert observed == [milliseconds]
    finally:
        connector.close()


@pytest.mark.parametrize(
    "reason, invalidated",
    [
        ("DPY-4024: timeout", True),
        ("DPI-1067: timeout", True),
        ("DPI-1080: connection closed", True),
        ("ORA-00900: invalid SQL statement", False),
    ],
)
def test_oracle_timeout_error_invalidates_connection(reason, invalidated):
    connector = OracleConnector({"username": "test", "service_name": "FREEPDB1"})
    try:
        engine = connector._ensure_engine()
        context = SimpleNamespace(original_exception=Exception(reason), is_disconnect=False)
        for handler in engine.dialect.dispatch.handle_error:
            handler(context)
        assert context.is_disconnect is invalidated
    finally:
        connector.close()
