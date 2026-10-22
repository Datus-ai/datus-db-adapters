import time

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_postgresql import PostgreSQLConnector
from datus_sqlalchemy.testing import BlackholeProxy, assert_stalled_response_timeout

pytestmark = pytest.mark.integration


def test_statement_deadline_cancels_query_and_connection_recovers(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, read_timeout_seconds=5, query_timeout_seconds=1)
    connector = PostgreSQLConnector(settings)
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
    assert_stalled_response_timeout(
        config,
        PostgreSQLConnector,
        operation,
        engine_getter=lambda connector: connector._get_engine(),
        settings_overrides={"query_timeout_seconds": 3},
        max_seconds=2.5,
    )


@pytest.mark.parametrize("engine_scope", ["base", "database"])
def test_reused_writer_validator_has_deadline_before_checkout_sql(config, engine_scope):
    """Exercise Aurora's real validator on PostgreSQL, without claiming AWS coverage."""
    from unittest.mock import patch

    from sqlalchemy import event, text
    from sqlalchemy.exc import DBAPIError

    from datus_sqlalchemy.auth import aurora_writer_validator

    with BlackholeProxy(config.host, config.port, safety_timeout=5) as proxy:
        settings = config.model_dump()
        settings.update(host="127.0.0.1", port=proxy.port, timeout_seconds=1, query_timeout_seconds=3)
        connector = PostgreSQLConnector(settings)
        writer_config = config.model_copy(
            update={"aurora": True, "host": "writer.cluster-example.us-east-1.rds.amazonaws.com"}
        )
        connector._connection_validator = aurora_writer_validator(writer_config, "postgresql")
        assert connector._connection_validator is not None
        engine = connector._ensure_engine() if engine_scope == "base" else connector._get_engine()
        invalidated, closed = [], []
        event.listen(engine, "invalidate", lambda connection, *args: invalidated.append(connection))
        event.listen(engine, "close", lambda connection, *args: closed.append(connection))

        def discard_checkout_reply(*args):
            # Pre-ping has already completed. Only the checkout probe loses its
            # reply, so a pre-ping timeout cannot accidentally satisfy the test.
            proxy.drop_responses.set()

        try:
            with engine.connect() as connection:
                first = connection.connection.dbapi_connection
                record = connection.connection._connection_record
                previous = record.info["datus_socket_deadline"]
                assert connection.execute(text("SELECT 42")).scalar_one() == 42
            assert previous.socket.fileno() == -1
            assert record.info == {}

            event.listen(engine, "checkout", discard_checkout_reply, insert=True)
            started = time.monotonic()
            with patch.object(engine.pool, "_creator", wraps=engine.pool._creator) as create:
                # libpq can close during execute; the validator's final rollback
                # then reports InterfaceError rather than OperationalError.
                with pytest.raises(DBAPIError):
                    engine.connect()
                create.assert_not_called()
            assert time.monotonic() - started < 2.5
            assert invalidated == [first]
            assert closed == [first]
            assert first.closed != 0
            assert record.info == {}
            assert proxy.wait_for_closed(None)

            event.remove(engine, "checkout", discard_checkout_reply)
            proxy.drop_responses.clear()
            with engine.connect() as connection:
                assert connection.connection.dbapi_connection is not first
                assert connection.execute(text("SELECT 42")).scalar_one() == 42
        finally:
            connector.close()
