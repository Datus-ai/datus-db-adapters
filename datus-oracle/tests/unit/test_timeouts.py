from unittest.mock import patch

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
