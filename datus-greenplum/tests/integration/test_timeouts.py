import time

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_greenplum import GreenplumConnector
from datus_sqlalchemy.testing import assert_query_timeout_override, assert_stalled_response_timeout

pytestmark = pytest.mark.integration


def test_statement_deadline_cancels_query_and_connection_recovers(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, read_timeout_seconds=5, query_timeout_seconds=1)
    connector = GreenplumConnector(settings)
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
        GreenplumConnector,
        operation,
        engine_getter=lambda connector: connector._get_engine(),
        settings_overrides={"query_timeout_seconds": 3},
        max_seconds=2.5,
    )


def test_query_timeout_override_is_scoped_to_one_select(config):
    assert_query_timeout_override(
        config,
        GreenplumConnector,
        "SELECT setting AS deadline FROM pg_settings WHERE name = 'statement_timeout'",
        units_per_second=1000,
    )
