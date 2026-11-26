import pytest
from TCLIService import TCLIService

from datus_hive import HiveConnector
from datus_sqlalchemy.testing import assert_stalled_response_timeout

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        HiveConnector,
        operation,
    )


def test_query_timeout_override_reaches_live_server_per_statement(config, monkeypatch):
    submitted = []
    sql = "SELECT 42 AS answer"
    execute = TCLIService.Client.ExecuteStatement

    def observe(client, request):
        if request.statement == sql:
            submitted.append(request.queryTimeout)
        return execute(client, request)

    monkeypatch.setattr(TCLIService.Client, "ExecuteStatement", observe)
    bounded = HiveConnector(config.model_copy(update={"timeout_seconds": 15, "query_timeout_seconds": 4}))
    try:
        for value in (None, 1, 8, None):
            result = bounded.execute_query(sql, result_format="list", query_timeout_seconds=value)
            assert result.success, result.error
            assert list(result.sql_return[0].values()) == [42]
        assert submitted == [4, 1, 8, 4]
    finally:
        bounded.close()
