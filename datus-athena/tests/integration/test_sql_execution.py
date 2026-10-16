import pytest

from datus_athena import AthenaConnector

pytestmark = pytest.mark.integration


@pytest.mark.acceptance
def test_unicode_and_null(connector):
    result = connector.execute({"sql_query": "SELECT '数据' AS text, CAST(NULL AS VARCHAR) AS missing"}, "list")
    assert result.success, result.error
    assert result.sql_return == [{"text": "数据", "missing": None}]


def test_invalid_sql_has_query_id_and_diagnostic(connector):
    result = connector.execute_query("SELECT * FROM datus_nonexistent_table_76ab")
    assert not result.success and result.error and result.query_execution_id


def test_actual_result_pagination(config):
    with AthenaConnector(config.model_copy(update={"page_size": 2})) as conn:
        result = conn.execute_query("SELECT x FROM UNNEST(ARRAY[1,2,3,4,5]) AS t(x) ORDER BY x", "list")
        assert result.success, result.error
        assert result.sql_return == [{"x": value} for value in range(1, 6)]


def test_native_cancellation_or_completion(connector):
    table = connector.full_name(table_name="tpch_region")
    query_id = connector._start(f"SELECT COUNT(*) FROM {table}", connector.catalog_name, connector.database_name, None)
    try:
        info = connector.cancel_query(query_id)
        assert info["cancellation_requested"], info
        assert info["state"] in {"CANCELLED", "SUCCEEDED"}, info
        assert info["cancellation_confirmed"] == (info["state"] == "CANCELLED")
    finally:
        # A failed cancellation assertion must not leave an executing test query.
        state = connector._client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]["State"]
        if state not in {"CANCELLED", "SUCCEEDED", "FAILED"}:
            connector._client.stop_query_execution(QueryExecutionId=query_id)
