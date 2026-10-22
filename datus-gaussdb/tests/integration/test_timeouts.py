import time

import pytest
from sqlalchemy import event

from datus_db_core import DatusDbException, ErrorCode
from datus_gaussdb import GaussDBConnector
from datus_sqlalchemy.testing import BlackholeProxy

pytestmark = pytest.mark.integration


def test_statement_deadline_cancels_query_and_connection_recovers(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, read_timeout_seconds=5, query_timeout_seconds=1)
    connector = GaussDBConnector(settings)
    try:
        assert connector.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            connector._execute_query("SELECT pg_sleep(3)")
        assert time.monotonic() - started < 2.5
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connector.execute_query("SELECT 42 AS answer", result_format="list").sql_return == [{"answer": 42}]
    finally:
        connector.close()


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_connection_is_invalidated(config, operation):
    with BlackholeProxy(config.host, config.port) as proxy:
        settings = config.model_dump()
        settings.update(host="127.0.0.1", port=proxy.port, timeout_seconds=1, query_timeout_seconds=3)
        connector = GaussDBConnector(settings)
        try:
            assert connector.test_connection()
            engine = connector._get_engine()
            invalidated = []
            event.listen(engine, "invalidate", lambda *args: invalidated.append(args))

            def blackhole_application_reply(conn, cursor, statement, *args):
                if not statement.startswith("SET search_path"):
                    proxy.drop_responses.set()

            event.listen(engine, "before_cursor_execute", blackhole_application_reply)
            started = time.monotonic()
            with pytest.raises(DatusDbException) as exc:
                if operation == "query":
                    connector._execute_query("SELECT 42 AS answer")
                elif operation == "metadata":
                    connector.get_tables()
                else:
                    connector.test_connection()
            assert time.monotonic() - started < 2.5
            assert exc.value.code == ErrorCode.DB_CONNECTION_TIMEOUT
            assert len(invalidated) == 1
            event.remove(engine, "before_cursor_execute", blackhole_application_reply)
            proxy.drop_responses.clear()
            assert connector.execute_query("SELECT 42 AS answer", result_format="list").sql_return == [{"answer": 42}]
        finally:
            connector.close()
