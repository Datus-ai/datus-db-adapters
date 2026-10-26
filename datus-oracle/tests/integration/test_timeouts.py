import time

import pytest

from datus_oracle import OracleConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation):
    assert_stalled_response_timeout(
        config,
        OracleConnector,
        operation,
        query="SELECT 42 AS answer FROM dual",
    )


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
