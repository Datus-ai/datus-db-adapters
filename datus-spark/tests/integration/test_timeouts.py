import time

import pytest

from datus_db_core import DatusDbException
from datus_spark import SparkConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        SparkConnector,
        operation,
    )


def test_real_server_enforces_query_timeout(config, connector):
    from datus_db_core import ErrorCode

    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    bounded = SparkConnector(settings)
    try:
        assert bounded.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            bounded._execute_query("SELECT SUM(SIN(id)) FROM range(1000000000)")
        assert time.monotonic() - started < 5
        assert exc.value.code in (ErrorCode.DB_EXECUTION_TIMEOUT, ErrorCode.DB_CONNECTION_TIMEOUT)
        assert bounded.test_connection()
    finally:
        bounded.close()
