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


def test_query_timeout_override_scope_sets_and_restores_session_property():
    from unittest.mock import MagicMock

    connector = TrinoConnector({"username": "test", "timeout_seconds": 15, "query_timeout_seconds": 4})
    try:
        assert connector.supports_query_timeout_override is True
        conn = MagicMock(invalidated=False)
        show_result = MagicMock()
        show_result.one.return_value = ("query_max_run_time", "6s", "100.00d")
        conn.exec_driver_sql.side_effect = [show_result, MagicMock(), MagicMock()]

        with connector._query_timeout_scope(conn, 12):
            pass

        assert [call.args[0] for call in conn.exec_driver_sql.call_args_list] == [
            "SHOW SESSION LIKE 'query_max_run_time'",
            "SET SESSION query_max_run_time = '12s'",
            "SET SESSION query_max_run_time = '6s'",
        ]
    finally:
        connector.close()
