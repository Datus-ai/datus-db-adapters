from unittest.mock import patch

import pytest

from datus_trino import TrinoConnector


def test_timeout_reaches_actual_driver():
    connector = TrinoConnector({"username": "test", "timeout_seconds": 7})
    try:
        engine = connector._ensure_engine()
        with patch("trino.dbapi.connect", side_effect=RuntimeError("DBAPI boundary")) as connect:
            with pytest.raises(RuntimeError, match="DBAPI boundary"):
                engine.pool._creator()
        for key, value in {"request_timeout": 7, "max_attempts": 1}.items():
            assert connect.call_args.kwargs.get(key) == value
    finally:
        connector.close()


@pytest.mark.parametrize("field", ["timeout_seconds", "query_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_trino import TrinoConfig

    with pytest.raises(ValidationError):
        TrinoConfig(**{"username": "test"}, **{field: value})
