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


@pytest.mark.parametrize("override", [None, 1])
def test_real_server_enforces_query_run_deadline(config, connector, override):

    from datus_db_core import ErrorCode

    settings = config.model_dump()
    settings.update(timeout_seconds=8, query_timeout_seconds=1 if override is None else 6)
    bounded = TrinoConnector(settings)
    try:
        assert bounded.test_connection()
        started = time.monotonic()
        sql = "SELECT sum(sin(cast(orderkey AS double))) FROM tpch.sf1000.orders"
        if override is None:
            with pytest.raises(DatusDbException) as exc:
                bounded._execute_query(sql)
            assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        else:
            result = bounded.execute_query(sql, query_timeout_seconds=override)
            assert not result.success
            assert ErrorCode.DB_EXECUTION_TIMEOUT.code in result.error, result.error
        assert time.monotonic() - started < 5
        session = bounded.execute_query("SHOW SESSION LIKE 'query_max_run_time'", result_format="list")
        assert session.success, session.error
        assert session.sql_return[0]["Value"] == ("1s" if override is None else "6s")
        assert bounded.test_connection()
    finally:
        bounded.close()
