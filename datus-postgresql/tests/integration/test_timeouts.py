import time

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_postgresql import PostgreSQLConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

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
