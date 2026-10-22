import time

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_doris import DorisConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation):
    assert_stalled_response_timeout(
        config,
        DorisConnector,
        operation,
    )


def test_real_server_enforces_query_deadline(config):
    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    connector = DorisConnector(settings)
    try:
        assert connector.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            connector._execute_query('SELECT SUM(SIN(number)) FROM numbers("number"="1000000000")')
        assert time.monotonic() - started < 4
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connector.test_connection()
    finally:
        connector.close()
