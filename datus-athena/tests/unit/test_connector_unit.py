import csv
import io
from contextvars import Context
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pytest

from datus_athena import AthenaConnector
from datus_db_core import ExecuteSQLResult

from .conftest import QUERY_ID, results, start, status, workgroup


def test_sdk_provider_configuration(monkeypatch, sdk):
    client, _ = sdk
    session = Mock()
    session.client.return_value = client
    factory = Mock(return_value=session)
    monkeypatch.setattr("boto3.Session", factory)
    conn = AthenaConnector(dict(region="us-east-1", profile_name="role", ca_bundle="/ca.pem", timeout_seconds=4))
    factory.assert_called_once_with(region_name="us-east-1", profile_name="role")
    assert session.client.call_args.args == ("athena",)
    kwargs = session.client.call_args.kwargs
    assert kwargs["verify"] == "/ca.pem"
    assert kwargs["config"].connect_timeout == kwargs["config"].read_timeout == 4
    assert kwargs["config"].retries == {"mode": "standard", "total_max_attempts": 3}
    assert conn.get_type() == conn.to_dict()["type"] == "athena"


def test_context_defaults_quoting_and_isolation(make_connector):
    conn = make_connector()
    assert conn.get_current_context() == dict(catalog_name="AwsDataCatalog", database_name="analytics", schema_name="")
    assert conn.full_name(table_name='"a""b"') == '"AwsDataCatalog"."analytics"."a""b"'
    assert conn.full_name(table_name="other.db.orders") == '"other"."db"."orders"'
    assert conn.full_name(table_name=".analytics.orders") == '"AwsDataCatalog"."analytics"."orders"'
    assert conn.full_name(table_name="..orders") == '"AwsDataCatalog"."analytics"."orders"'
    assert conn.quote_identifier('a"b') == '"a""b"'
    assert conn.default_catalog() == "AwsDataCatalog"
    conn.switch_context(catalog_name="new", database_name="next")
    assert conn.get_current_context() == dict(catalog_name="new", database_name="next", schema_name="")
    assert Context().run(conn.get_current_context) == dict(
        catalog_name="AwsDataCatalog", database_name="analytics", schema_name=""
    )
    with pytest.raises(ValueError, match="schema_name"):
        conn.switch_context(schema_name="invalid")
    with pytest.raises(ValueError, match="table_name"):
        conn.full_name()
    conn.switch_catalog("third")
    assert conn.catalog_name == "third"


def test_close_swallows_driver_error_and_reconnects(make_connector, monkeypatch):
    conn = make_connector()
    old = conn._client
    monkeypatch.setattr(old, "close", Mock(side_effect=RuntimeError("close failed")))
    conn.close()
    conn.close()
    assert conn._client is None
    conn.connect()
    assert conn._client is old


@pytest.mark.parametrize(
    "configuration,output,expected",
    [
        ({}, None, {}),
        ({}, "s3://bucket/results/", {"ResultConfiguration": {"OutputLocation": "s3://bucket/results/"}}),
        ({"EnforceWorkGroupConfiguration": True}, "s3://ignored/", {}),
        ({"ManagedQueryResultsConfiguration": {"Enabled": True}}, "s3://ignored/", {}),
    ],
)
def test_workgroup_output_precedence(make_connector, sdk, configuration, output, expected):
    conn = make_connector(output_location=output)
    _, stubber = sdk
    start(stubber, configuration=configuration, **expected)
    status(stubber)
    results(stubber, [[1]])
    result = conn.execute_query("SELECT 1", "list")
    assert isinstance(result, ExecuteSQLResult)
    assert result.success and result.sql_return == [{"value": 1}]
    assert result.query_execution_id == QUERY_ID
    assert result.execution_info["statistics"] == {"DataScannedInBytes": 42}


@pytest.mark.parametrize("fmt", ["list", "arrow", "pandas", "csv"])
def test_types_and_formats(make_connector, sdk, fmt):
    conn = make_connector()
    _, stubber = sdk
    columns = [
        dict(Name=name, Type=kind)
        for name, kind in [
            ("id", "bigint"),
            ("text", "varchar"),
            ("empty", "varchar"),
            ("null", "varchar"),
            ("amount", "decimal(10,2)"),
            ("day", "date"),
            ("ts", "timestamp"),
            ("flag", "boolean"),
            ("bin", "varbinary"),
            ("real", "double"),
            ("array", "array"),
            ("zoned", "timestamp with time zone"),
        ]
    ]
    values = [
        1,
        "数据",
        "",
        None,
        "123.45",
        "2024-02-03",
        "2024-02-03 04:05:06.123456",
        "true",
        "00 ff",
        "2.5",
        "[1, 2]",
        "2024-02-03 04:05:06 UTC",
    ]
    start(stubber)
    status(stubber)
    results(stubber, [values], columns)
    result = conn.execute_query("SELECT 1", fmt)
    assert result.success, result.error
    assert result.row_count == 1 and result.result_format == fmt
    if fmt == "list":
        assert result.sql_return == [
            dict(
                zip(
                    [c["Name"] for c in columns],
                    [
                        1,
                        "数据",
                        "",
                        None,
                        Decimal("123.45"),
                        date(2024, 2, 3),
                        datetime(2024, 2, 3, 4, 5, 6, 123456),
                        True,
                        b"\x00\xff",
                        2.5,
                        "[1, 2]",
                        "2024-02-03 04:05:06 UTC",
                    ],
                    strict=True,
                )
            )
        ]
    elif fmt == "arrow":
        assert result.sql_return.schema.field("amount").type == pa.decimal128(10, 2)
        assert result.sql_return["text"].to_pylist() == ["数据"]
        assert result.sql_return["null"].null_count == 1
    elif fmt == "pandas":
        assert isinstance(result.sql_return, pd.DataFrame)
        assert result.sql_return.iloc[0]["amount"] == Decimal("123.45")
        assert result.sql_return.iloc[0]["text"] == "数据"
    else:
        assert "数据" in result.sql_return and "123.45" in result.sql_return
        assert result.sql_return.startswith("id,text,empty,null,amount,day,ts,flag,bin,real,array,zoned\n")


@pytest.mark.parametrize("fmt", ["list", "arrow", "pandas", "csv"])
def test_nullable_bigints_keep_exact_values_in_all_formats(make_connector, sdk, fmt):
    _, stubber = sdk
    values = [2**53 + 1, None, 2**63 - 1, -(2**63), -(2**53 + 1)]
    sql = "SELECT id FROM test_values"
    start(stubber, sql)
    status(stubber)
    results(stubber, [[value] for value in values], [{"Name": "id", "Type": "bigint"}])
    result = make_connector().execute_query(sql, fmt)
    assert result.success, result.error
    if fmt == "list":
        assert result.sql_return == [{"id": value} for value in values]
    elif fmt == "arrow":
        assert result.sql_return["id"].to_pylist() == values
    elif fmt == "pandas":
        assert result.sql_return["id"].tolist() == values
    else:
        assert list(csv.DictReader(io.StringIO(result.sql_return))) == [
            {"id": "" if value is None else str(value)} for value in values
        ]


def test_result_pages_skip_header_only_once(make_connector, sdk):
    conn = make_connector(page_size=2)
    _, stubber = sdk
    start(stubber)
    status(stubber, "QUEUED")
    status(stubber, "RUNNING")
    status(stubber)
    columns = [{"Name": "value", "Type": "varchar"}]
    results(stubber, [["first"]], columns, next_token="page2", page_size=2)
    results(stubber, [["value"], ["数据"]], columns, token="page2", page_size=2, header=False)
    result = conn.execute({"sql_query": "SELECT 1"}, "list")
    assert result.success and result.sql_return == [{"value": "first"}, {"value": "value"}, {"value": "数据"}]
    assert result.execution_info["truncated"] is False


@pytest.mark.parametrize("sql", ["SHOW CREATE TABLE `orders`", "EXPLAIN SELECT 1"])
def test_utility_first_row_is_data(make_connector, sdk, sql):
    conn = make_connector()
    _, stubber = sdk
    start(stubber, sql)
    status(stubber, statement_type="UTILITY")
    results(
        stubber, [["CREATE EXTERNAL TABLE orders ("]], [{"Name": "createtab_stmt", "Type": "varchar"}], header=False
    )
    result = conn.execute_explain(sql, "list", database_name="analytics")
    assert result.success and result.sql_return == [{"createtab_stmt": "CREATE EXTERNAL TABLE orders ("}]


@pytest.mark.parametrize(
    "limit,rows,next_token,truncated",
    [
        (1, [[1], [2]], None, True),
        (1, [[1]], "more", True),
        (1, [[1]], None, False),
    ],
)
def test_row_budget(make_connector, sdk, limit, rows, next_token, truncated):
    conn = make_connector(max_rows=limit)
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, rows, next_token=next_token)
    result = conn.execute_query("SELECT 1", "list")
    assert result.sql_return == [{"value": 1}]
    assert result.execution_info["truncated"] is truncated
    if truncated:
        assert result.execution_info["truncation_reason"] == "max_rows"


def test_utf8_byte_budget(make_connector, sdk):
    conn = make_connector(max_result_bytes=6)
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [["数据"], ["x"]], [{"Name": "value", "Type": "varchar"}])
    result = conn.execute_query("SELECT 1", "list")
    assert result.success and result.sql_return == [{"value": "数据"}]
    assert result.execution_info["truncation_reason"] == "max_result_bytes"
    assert result.execution_info["result_bytes"] == 6


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED"])
def test_terminal_failure_is_not_retried(make_connector, sdk, state):
    conn = make_connector()
    _, stubber = sdk
    start(stubber)
    status(
        stubber,
        Status={
            "State": state,
            "StateChangeReason": "permission denied",
            "AthenaError": {"ErrorCategory": 2, "ErrorType": 1006, "Retryable": True},
        },
    )
    result = conn.execute_query("SELECT 1")
    assert not result.success and state in result.error and QUERY_ID in result.error
    assert result.execution_info["athena_error"]["Retryable"] is True


def test_submit_permission_error(make_connector, sdk):
    _, stubber = sdk
    workgroup(stubber)
    stubber.add_client_error(
        "start_query_execution", service_error_code="AccessDeniedException", service_message="Denied"
    )
    result = make_connector().execute_query("SELECT 1")
    assert not result.success and "Denied" in result.error and result.query_execution_id is None


def test_poll_failure_cancels_remote_query(make_connector, sdk):
    _, stubber = sdk
    start(stubber)
    stubber.add_client_error("get_query_execution", service_error_code="InternalServerException")
    stubber.add_response("stop_query_execution", {}, {"QueryExecutionId": QUERY_ID})
    status(stubber, "CANCELLED")
    result = make_connector().execute_query("SELECT 1")
    assert not result.success
    assert result.execution_info["cancellation_requested"] is True
    assert result.execution_info["cancellation_confirmed"] is True


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.mark.parametrize("cancel_state,confirmed", [("CANCELLED", True), ("RUNNING", False), ("SUCCEEDED", False)])
def test_wait_timeout_and_cancel_observation(make_connector, sdk, monkeypatch, cancel_state, confirmed):
    clock = Clock()
    monkeypatch.setattr("datus_athena.connector.time", clock)
    conn = make_connector(query_timeout_seconds=1, cancel_timeout_seconds=1, poll_interval_seconds=1)
    _, stubber = sdk
    start(stubber)
    status(stubber, "RUNNING")
    status(stubber, "RUNNING")
    stubber.add_response("stop_query_execution", {}, {"QueryExecutionId": QUERY_ID})
    status(stubber, cancel_state)
    result = conn.execute_query("SELECT 1")
    assert not result.success and "deadline" in result.error
    assert result.execution_info["state"] == cancel_state
    assert result.execution_info["cancellation_requested"] is True
    assert result.execution_info["cancellation_confirmed"] is confirmed


def test_cancel_failure_is_preserved(make_connector, sdk):
    _, stubber = sdk
    stubber.add_client_error(
        "stop_query_execution", service_error_code="AccessDeniedException", service_message="No cancel"
    )
    result = make_connector().cancel_query(QUERY_ID)
    assert result["cancellation_requested"] is False and result["cancellation_confirmed"] is False
    assert "No cancel" in result["cancellation_error"]


def test_cancel_request_only(make_connector, sdk):
    _, stubber = sdk
    stubber.add_response("stop_query_execution", {}, {"QueryExecutionId": QUERY_ID})
    result = make_connector(cancel_timeout_seconds=0).cancel_query(QUERY_ID)
    assert result["cancellation_requested"] is True and result["cancellation_confirmed"] is False


def test_result_deadline_does_not_cancel_completed_query(make_connector, sdk, monkeypatch):
    conn = make_connector(result_timeout_seconds=1)
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [[1]])
    monkeypatch.setattr("datus_athena.connector.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 0, 0, 2])))
    result = conn.execute_query("SELECT 1")
    assert not result.success and "result read deadline" in result.error
    assert result.execution_info["state"] == "SUCCEEDED"


def test_token_and_context_are_passed_unchanged(make_connector, sdk):
    _, stubber = sdk
    token = "token-" + "0" * 32
    for _ in range(2):
        start(stubber, context={"Catalog": "other", "Database": "db"}, token=token)
        status(stubber)
        results(stubber, [[1]])
    conn = make_connector()
    for _ in range(2):
        result = conn.execute_query("SELECT 1", "list", "other", "db", client_request_token=token)
        assert result.success and result.query_execution_id == QUERY_ID
    assert conn.database_name == "analytics"


def test_ddl_dml_and_session_statement(make_connector, sdk):
    _, stubber = sdk
    conn = make_connector()
    start(stubber, "CREATE TABLE t (id int)")
    status(stubber, statement_type="DDL")
    assert conn.execute_ddl("CREATE TABLE t (id int)").success
    for sql, method in [
        ("INSERT INTO t VALUES (1)", conn.execute_insert),
        ("UPDATE t SET id=2", conn.execute_update),
        ("DELETE FROM t", conn.execute_delete),
    ]:
        start(stubber, sql)
        status(stubber)
        stubber.add_response("get_query_results", {"UpdateCount": 1}, {"QueryExecutionId": QUERY_ID, "MaxResults": 1})
        result = method(sql)
        assert result.success and result.row_count == 1 and result.sql_return is None
    assert not conn.execute_content_set("BEGIN").success


@pytest.mark.parametrize("fmt", ["arrow", "pandas", "csv"])
def test_format_shortcuts(make_connector, sdk, fmt):
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [[1]])
    result = getattr(make_connector(), f"execute_{fmt}")("SELECT 1")
    assert result.success and result.row_count == 1 and result.result_format == fmt


def test_csv_iterator_and_batch(make_connector, sdk):
    _, stubber = sdk
    conn = make_connector()
    start(stubber)
    status(stubber)
    results(stubber, [[1], [None]])
    assert list(conn.execute_csv_iterator("SELECT 1", max_rows=2)) == [("value",), ("1",), ("",)]
    assert list(conn.execute_csv_iterator("SELECT 1", max_rows=0)) == []
    with pytest.raises(ValueError, match="negative"):
        list(conn.execute_csv_iterator("SELECT 1", max_rows=-1))
    start(stubber)
    status(stubber)
    results(stubber, [[1]])
    assert conn.execute_queries(["SELECT 1"])[0].success


@pytest.mark.parametrize(
    "kwargs,match", [({"result_format": "json"}, "format"), ({"schema_name": "invalid"}, "schema_name")]
)
def test_invalid_execution_has_no_sdk_calls(make_connector, kwargs, match):
    result = make_connector().execute_query("SELECT 1", **kwargs)
    assert not result.success and match in result.error


def test_disabled_workgroup(make_connector, sdk):
    _, stubber = sdk
    workgroup(stubber, state="DISABLED")
    result = make_connector().execute_query("SELECT 1")
    assert not result.success and "not enabled" in result.error


@pytest.mark.parametrize(
    "rows,columns,match",
    [
        ([["not-boolean"]], [{"Name": "flag", "Type": "boolean"}], "boolean"),
        ([[1, 2]], [{"Name": "value", "Type": "integer"}], "width"),
        ([[1, 2]], [{"Name": "value", "Type": "integer"}] * 2, "unique"),
    ],
)
def test_malformed_or_lossy_list_results_fail(make_connector, sdk, rows, columns, match):
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, rows, columns)
    result = make_connector().execute_query("SELECT 1", "list")
    assert not result.success and match in result.error


def test_repeated_result_token_fails(make_connector, sdk):
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [[1]], next_token="repeat")
    results(stubber, [[2]], token="repeat", next_token="repeat", header=False)
    result = make_connector().execute_query("SELECT 1")
    assert not result.success and "pagination token" in result.error


def test_empty_select_keeps_typed_schema(make_connector, sdk):
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [], [{"Name": "amount", "Type": "decimal", "Precision": 18, "Scale": 4}])
    result = make_connector().execute_arrow("SELECT 1")
    assert result.success and result.row_count == 0
    assert result.sql_return.schema == pa.schema([("amount", pa.decimal128(18, 4))])


def test_high_precision_timestamp_is_not_silently_truncated(make_connector, sdk):
    _, stubber = sdk
    start(stubber)
    status(stubber)
    results(stubber, [["2024-02-03 04:05:06.123456789"]], [{"Name": "ts", "Type": "timestamp"}])
    result = make_connector().execute_query("SELECT 1", "list")
    assert not result.success and "precision" in result.error and "VARCHAR" in result.error


def test_spark_workgroup_is_rejected_before_submission(make_connector, sdk):
    _, stubber = sdk
    workgroup(stubber, {"EngineVersion": {"SelectedEngineVersion": "PySpark engine version 3"}})
    result = make_connector().execute_query("SELECT 1")
    assert not result.success and "Spark" in result.error


def test_cached_athena_client_signs_with_refreshed_credentials(make_connector, sdk):
    from datetime import timedelta, timezone
    from urllib.parse import parse_qs, urlsplit

    from botocore.credentials import RefreshableCredentials

    now = [datetime(2026, 10, 16, tzinfo=timezone.utc)]
    refreshes = []

    def refresh():
        refreshes.append(True)
        return dict(
            access_key="RENEWED_TEST_KEY",
            secret_key="renewed_test_secret",
            token="renewed_test_token",
            expiry_time=(now[0] + timedelta(hours=1)).isoformat(),
        )

    credentials = RefreshableCredentials(
        access_key="INITIAL_TEST_KEY",
        secret_key="initial_test_secret",
        token="initial_test_token",
        expiry_time=now[0] + timedelta(hours=1),
        refresh_using=refresh,
        method="offline-test",
        time_fetcher=lambda: now[0],
    )
    client, _ = sdk
    client._request_signer._credentials = credentials
    conn = make_connector()

    def signed_identity():
        url = conn._client.generate_presigned_url("get_query_execution", Params={"QueryExecutionId": QUERY_ID})
        return parse_qs(urlsplit(url).query)["X-Amz-Credential"][0].split("/")[0]

    assert signed_identity() == "INITIAL_TEST_KEY"
    now[0] += timedelta(hours=2)
    assert signed_identity() == "RENEWED_TEST_KEY"
    assert refreshes == [True]
