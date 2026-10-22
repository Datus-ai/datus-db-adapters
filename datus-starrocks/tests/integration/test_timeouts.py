import time

import pytest
from sqlalchemy import event

from datus_db_core import DatusDbException, ErrorCode
from datus_sqlalchemy.testing import BlackholeProxy
from datus_starrocks import StarRocksConnector

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation):
    with BlackholeProxy(config.host, config.port) as proxy:
        settings = config.model_dump()
        settings.update(host="127.0.0.1", port=proxy.port, timeout_seconds=1)
        connector = StarRocksConnector(settings)
        try:
            assert connector.test_connection()
            engine = connector._ensure_engine()

            def discard_reply(conn, cursor, statement, *args):
                if not statement.upper().startswith(("SET ", "USE ", "ALTER SESSION")):
                    proxy.drop_responses.set()

            event.listen(engine, "before_cursor_execute", discard_reply)
            started = time.monotonic()
            with pytest.raises(DatusDbException):
                if operation == "query":
                    connector._execute_query("SELECT 42 AS answer")
                elif operation == "metadata":
                    connector.get_tables()
                else:
                    connector.test_connection()
            assert time.monotonic() - started < 5
            event.remove(engine, "before_cursor_execute", discard_reply)
            proxy.drop_responses.clear()
            assert connector.execute_query("SELECT 42 AS answer").success is True
        finally:
            connector.close()


def test_real_server_enforces_query_deadline(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    connector = StarRocksConnector(settings)
    try:
        assert connector.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            connector._execute_query("SELECT SUM(SIN(generate_series)) FROM TABLE(generate_series(1, 1000000000))")
        assert time.monotonic() - started < 4
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connector.test_connection()
    finally:
        connector.close()
