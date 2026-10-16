import os

import pytest

from datus_athena import AthenaConnector

pytestmark = pytest.mark.integration


@pytest.mark.acceptance
def test_connection_context_and_result_storage(config):
    with AthenaConnector(config.model_dump()) as connector:
        assert connector.test_connection()
        result = connector.execute_query("SELECT 1 AS value", "list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 1}]
        assert result.query_execution_id
        assert result.execution_info["state"] == "SUCCEEDED"
        expected = os.getenv("ATHENA_EXPECT_MANAGED_RESULTS")
        if expected is not None:
            assert expected.lower() in {"true", "false"}
            assert result.execution_info["managed_results"] == (expected.lower() == "true")


def test_denied_aws_identity(denied_connector):
    assert denied_connector.catalog_name in denied_connector.get_catalogs()
    result = denied_connector.execute_query("SELECT 1")
    assert not result.success and "accessdenied" in result.error.lower()
    assert result.query_execution_id is None


def test_environment_specific_kms_or_lakeformation_denial(failure_connector, failure_sql):
    assert failure_connector.test_connection()
    result = failure_connector.execute_query(failure_sql)
    assert not result.success and result.query_execution_id and result.error
