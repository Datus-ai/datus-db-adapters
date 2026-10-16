import os
from urllib.parse import urlsplit

import pytest

from datus_athena import AthenaConnector

from .http_faults import AthenaFaultProxy

pytestmark = pytest.mark.integration


@pytest.fixture
def fault_proxy(config, tmp_path):
    if os.getenv("ATHENA_RUN_NETWORK_FAULT_TESTS") != "1":
        pytest.skip("Set ATHENA_RUN_NETWORK_FAULT_TESTS=1 for isolated loopback TLS faults")
    with AthenaConnector(config) as connector:
        hostname = urlsplit(connector._client.meta.endpoint_url).hostname
    with AthenaFaultProxy(tmp_path, hostname) as proxy:
        yield proxy


def test_sdk_retries_transport_throttle_without_resubmitting_query(config, fault_proxy):
    fault_proxy.scenario = "throttle"
    with fault_proxy.connector(config) as connector:
        result = connector.execute_query("SELECT 1 AS value", "list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 1}]
        assert fault_proxy.injected == 1
        assert fault_proxy.counts["GetQueryExecution"] >= 2
        assert fault_proxy.starts
        assert len({token for token, _ in fault_proxy.starts}) == 1
        assert {query_id for _, query_id in fault_proxy.starts} == {result.query_execution_id}


def test_lost_submit_response_retries_with_native_idempotency(config, fault_proxy):
    fault_proxy.scenario = "lost_submit_response"
    with fault_proxy.connector(config) as connector:
        result = connector.execute_query("SELECT 1 AS value", "list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 1}]
        assert fault_proxy.injected == 1
        assert len(fault_proxy.starts) >= 2
        assert len({token for token, _ in fault_proxy.starts}) == 1
        assert {query_id for _, query_id in fault_proxy.starts} == {result.query_execution_id}


def test_socket_timeout_cancels_then_same_client_recovers(config, fault_proxy):
    fault_proxy.scenario = "read_timeout"
    fault_proxy.read_delay_seconds = 7
    with fault_proxy.connector(config, timeout_seconds=3, cancel_timeout_seconds=1) as connector:
        client = connector._client
        failed = connector.execute_query("SELECT 1 AS value", "list")
        assert not failed.success
        assert "timeout" in failed.error.lower()
        assert failed.query_execution_id
        assert failed.execution_info["cancellation_requested"]
        assert not failed.execution_info["cancellation_confirmed"]
        assert fault_proxy.injected >= 3
        assert fault_proxy.starts
        assert len({token for token, _ in fault_proxy.starts}) == 1
        assert {query_id for _, query_id in fault_proxy.starts} == {failed.query_execution_id}
        fault_proxy.scenario = "none"
        state = client.get_query_execution(QueryExecutionId=failed.query_execution_id)["QueryExecution"]["Status"][
            "State"
        ]
        assert state in {"SUCCEEDED", "CANCELLED"}
        recovered = connector.execute_query("SELECT 2 AS value", "list")
        assert recovered.success, recovered.error
        assert recovered.sql_return == [{"value": 2}]
        assert connector._client is client


def test_real_query_wait_deadline_requests_cancellation(config):
    with AthenaConnector(config.model_copy(update={"query_timeout_seconds": 0.001})) as connector:
        result = connector.execute_query("SELECT SUM(r_regionkey) FROM tpch_region", "list")
        assert not result.success
        assert "query wait deadline" in result.error.lower()
        assert result.query_execution_id
        assert result.execution_info["cancellation_requested"]
        assert result.execution_info["state"] in {"SUCCEEDED", "CANCELLED"}


def test_real_result_deadline_retains_completed_query_id(config):
    with AthenaConnector(config.model_copy(update={"result_timeout_seconds": 0.001})) as connector:
        result = connector.execute_query("SELECT 1 AS value", "list")
        assert not result.success
        assert "result read deadline" in result.error.lower()
        assert result.query_execution_id
        assert result.execution_info["state"] == "SUCCEEDED"


@pytest.mark.parametrize(
    "budget,reason", [({"max_rows": 1}, "max_rows"), ({"max_result_bytes": 1}, "max_result_bytes")]
)
def test_real_result_budgets_report_truncation(config, budget, reason):
    with AthenaConnector(config.model_copy(update=budget)) as connector:
        result = connector.execute_query("SELECT r_name FROM tpch_region ORDER BY r_regionkey", "list")
        assert result.success, result.error
        assert result.execution_info["truncated"]
        assert result.execution_info["truncation_reason"] == reason
        assert result.query_execution_id
