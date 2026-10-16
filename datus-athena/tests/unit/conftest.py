from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import ANY, Stubber

from datus_athena import AthenaConnector

QUERY_ID = "datus-offline-query-id"


@pytest.fixture
def sdk():
    client = boto3.client(
        "athena", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing"
    )
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()
    client.close()


@pytest.fixture
def make_connector(sdk):
    client, _ = sdk

    def make(**kwargs):
        config = dict(region="us-east-1", workgroup="datus-test", database="analytics", poll_interval_seconds=0.001)
        config.update(kwargs)
        return AthenaConnector(config, session=SimpleNamespace(client=lambda *args, **kwargs: client))

    return make


def workgroup(stubber, configuration=None, state="ENABLED"):
    stubber.add_response(
        "get_work_group",
        {"WorkGroup": {"Name": "datus-test", "State": state, "Configuration": configuration or {}}},
        {"WorkGroup": "datus-test"},
    )


def status(stubber, state="SUCCEEDED", statement_type="DML", **kwargs):
    execution = {
        "QueryExecutionId": QUERY_ID,
        "StatementType": statement_type,
        "Status": {"State": state},
        "WorkGroup": "datus-test",
        "Statistics": {"DataScannedInBytes": 42},
    }
    execution.update(kwargs)
    stubber.add_response("get_query_execution", {"QueryExecution": execution}, {"QueryExecutionId": QUERY_ID})


def start(stubber, sql="SELECT 1", configuration=None, context=None, token=ANY, **request_extra):
    workgroup(stubber, configuration)
    request = dict(
        QueryString=sql,
        ClientRequestToken=token,
        WorkGroup="datus-test",
        QueryExecutionContext=context or {"Catalog": "AwsDataCatalog", "Database": "analytics"},
    )
    request.update(request_extra)
    stubber.add_response("start_query_execution", {"QueryExecutionId": QUERY_ID}, request)


def results(stubber, rows, columns=None, *, token=None, next_token=None, page_size=1000, header=True, **extra):
    columns = columns if columns is not None else [{"Name": "value", "Type": "integer"}]
    all_rows = [[c["Name"] for c in columns]] + rows if header else rows
    response = {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": columns},
            "Rows": [
                {"Data": [{} if cell is None else {"VarCharValue": str(cell)} for cell in row]} for row in all_rows
            ],
        },
        **extra,
    }
    if next_token:
        response["NextToken"] = next_token
    request = {"QueryExecutionId": QUERY_ID, "MaxResults": page_size}
    if token:
        request["NextToken"] = token
    stubber.add_response("get_query_results", response, request)
