import time

import pytest

from datus_clickhouse import ClickHouseConnector
from datus_db_core import DatusDbException, ErrorCode
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, metadata_objects_setup):
    assert_stalled_response_timeout(
        config,
        ClickHouseConnector,
        operation,
    )


def test_real_server_enforces_query_deadline(config, metadata_objects_setup):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    connector = ClickHouseConnector(settings)
    try:
        assert connector.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            connector._execute_query("SELECT sum(sin(number)) FROM numbers(1000000000)")
        assert time.monotonic() - started < 4
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connector.test_connection()
    finally:
        connector.close()


def test_query_timeout_override_is_scoped_to_one_select(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=15, query_timeout_seconds=4)
    connector = ClickHouseConnector(settings)
    sql = "SELECT getSetting('max_execution_time') AS deadline"
    try:
        cases = (
            (sql, None, 4),
            (sql, 1, 1),
            (sql, 8, 8),
            (sql + " SETTINGS max_execution_time=6", 1, 6),
            (sql, None, 4),
        )
        for query, override, expected in cases:
            result = connector.execute_query(query, result_format="list", query_timeout_seconds=override)
            assert result.success, result.error
            assert float(result.sql_return[0]["deadline"]) == expected
    finally:
        connector.close()
