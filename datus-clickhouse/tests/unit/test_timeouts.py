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
