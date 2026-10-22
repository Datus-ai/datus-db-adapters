import time

import pytest

from datus_db_core import DatusDbException
from datus_sqlalchemy.testing import assert_stalled_response_timeout
from datus_trino import TrinoConnector

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        TrinoConnector,
        operation,
    )


def test_real_server_enforces_query_run_deadline(config, connector):

    from datus_db_core import ErrorCode

    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    bounded = TrinoConnector(settings)
    try:
        assert bounded.test_connection()
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            bounded._execute_query("SELECT sum(sin(cast(orderkey AS double))) FROM tpch.sf1000.orders")
        assert time.monotonic() - started < 5
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert bounded.test_connection()
    finally:
        bounded.close()
