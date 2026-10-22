import time

import pytest
from sqlalchemy import event

from datus_db_core import DatusDbException
from datus_oracle import OracleConnector
from datus_sqlalchemy.testing import BlackholeProxy

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation):
    with BlackholeProxy(config.host, config.port) as proxy:
        settings = config.model_dump()
        settings.update(host="127.0.0.1", port=proxy.port, timeout_seconds=1)
        connector = OracleConnector(settings)
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
                    connector._execute_query("SELECT 42 AS answer FROM dual")
                elif operation == "metadata":
                    connector.get_tables()
                else:
                    connector.test_connection()
            assert time.monotonic() - started < 5
            event.remove(engine, "before_cursor_execute", discard_reply)
            proxy.drop_responses.clear()
            assert connector.execute_query("SELECT 42 AS answer FROM dual").success is True
        finally:
            connector.close()


def test_real_database_round_trip_timeout(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=1)
    connector = OracleConnector(settings)
    try:
        assert connector.test_connection()
        started = time.monotonic()
        result = connector.execute_ddl("BEGIN DBMS_SESSION.SLEEP(3); END;")
        assert result.success is False
        assert "timeout" in result.error.lower() or "DPY-4024" in result.error
        assert time.monotonic() - started < 5
        assert connector.test_connection()
    finally:
        connector.close()
