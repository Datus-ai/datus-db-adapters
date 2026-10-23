import time

import pytest

from datus_db_core import DatusDbException
from datus_mysql import MySQLConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        MySQLConnector,
        operation,
    )


def test_server_select_deadline(config, connector):
    import uuid

    from datus_db_core import ErrorCode

    settings = config.model_dump()
    settings.update(timeout_seconds=5, query_timeout_seconds=1)
    bounded = MySQLConnector(settings)
    table = f"deadline_{uuid.uuid4().hex[:8]}"
    try:
        assert bounded.execute_ddl(f"CREATE TABLE `{table}` (id INT PRIMARY KEY)").success
        assert bounded.execute_insert(f"INSERT INTO `{table}` VALUES " + ",".join(f"({i})" for i in range(10))).success
        assert bounded.execute_query("SELECT @@max_execution_time AS deadline", result_format="list").sql_return == [
            {"deadline": 1000}
        ]
        sources = " CROSS JOIN ".join(f"`{table}` p{i}" for i in range(9))
        expression = "+".join(f"p{i}.id" for i in range(9))
        started = time.monotonic()
        with pytest.raises(DatusDbException) as exc:
            bounded._execute_query(f"SELECT SUM(SIN({expression})) FROM {sources}")
        assert time.monotonic() - started < 4
        assert exc.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert bounded.test_connection()
    finally:
        bounded.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
        bounded.close()
