from unittest.mock import patch

import pytest

from datus_spark import SparkConnector


def test_thrift_transport_receives_socket_deadline():
    connector = SparkConnector({"username": "test", "auth_mechanism": "NOSASL", "timeout_seconds": 7})
    try:
        engine = connector._ensure_engine()
        with patch("pyhive.hive.connect", side_effect=RuntimeError("DBAPI boundary")) as connect:
            for _ in range(2):
                with pytest.raises(RuntimeError, match="DBAPI boundary"):
                    engine.pool._creator()
        assert connect.call_count == 2
        assert (
            connect.call_args_list[0].kwargs["thrift_transport"]
            is not connect.call_args_list[1].kwargs["thrift_transport"]
        )
        transport = connect.call_args.kwargs.get("thrift_transport")
        assert transport is not None
        assert transport._TBufferedTransport__trans._timeout == 7
        assert "host" not in connect.call_args.kwargs
        assert "auth" not in connect.call_args.kwargs
    finally:
        connector.close()


@pytest.mark.parametrize("field", ["timeout_seconds", "query_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_spark import SparkConfig

    with pytest.raises(ValidationError):
        SparkConfig(**{"username": "test"}, **{field: value})


def test_query_timeout_override_capability_and_thrift_scope():
    from unittest.mock import MagicMock

    from datus_sqlalchemy.thrift import _query_timeout

    connector = SparkConnector({"username": "test", "auth_mechanism": "NOSASL"})
    try:
        assert connector.supports_query_timeout_override is True
        assert _query_timeout.get() is None
        with connector._query_timeout_scope(MagicMock(), 13):
            assert _query_timeout.get() == 13
        assert _query_timeout.get() is None
    finally:
        connector.close()
