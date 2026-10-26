import boto3
import pytest
import sqlglot
from botocore.stub import Stubber

from datus_db_core.testing.contract import assert_table_contract

from ..integration.test_contract import build_writable_contract, delete_contract_data, writable_location
from .conftest import QUERY_ID, results, start, status
from .test_metadata import metadata


def test_writable_contract_runs_the_shared_assertions_through_the_sdk(make_connector, sdk):
    _, stubber = sdk
    connector = make_connector()
    case = build_writable_contract(connector, "contract_unit", "s3://fixture-bucket/contracts/contract_unit/")
    expected_columns = (
        "id",
        "Mixed Case",
        "special-name",
        "nullable_text",
        "event_date",
        "event_ts",
        "amount",
        "bool_flag",
    )
    assert case.expected_columns == expected_columns
    create = sqlglot.parse_one(case.create_sql, read="athena")
    assert tuple(column.this.name for column in create.this.expressions) == expected_columns
    assert all(column.this.args.get("quoted") for column in create.this.expressions)
    insert = sqlglot.parse_one(case.insert_sqls[0], read="athena")
    assert tuple(column.name for column in insert.this.expressions) == expected_columns
    assert all(column.args.get("quoted") for column in insert.this.expressions)
    select = sqlglot.parse_one(case.qualified_select_sql, read="athena")
    assert [
        (column.name, column.this.args.get("quoted"))
        for column in select.find_all(sqlglot.exp.Column)
        if column.name in {"Mixed Case", "special-name"}
    ] == [("Mixed Case", True), ("special-name", True)]
    for sql in (case.create_sql, *case.insert_sqls, case.qualified_select_sql, case.limit_sql, case.drop_sql):
        assert sqlglot.parse_one(sql, read="athena") is not None
    for sql in (case.drop_sql, case.create_sql):
        start(stubber, sql)
        status(stubber, statement_type="DDL")
    start(stubber, case.insert_sqls[0])
    status(stubber)
    stubber.add_response("get_query_results", {"UpdateCount": 2}, {"QueryExecutionId": QUERY_ID, "MaxResults": 1})
    types = ("bigint", "varchar", "varchar", "varchar", "date", "timestamp", "decimal(10,2)", "boolean")
    metadata(
        stubber,
        dict(
            Name=case.table_name,
            TableType="EXTERNAL_TABLE",
            Columns=[{"Name": name, "Type": kind} for name, kind in zip(case.expected_columns, types, strict=True)],
        ),
    )
    metadata(stubber, dict(Name=case.table_name, TableType="EXTERNAL_TABLE"))
    start(stubber, f"SHOW CREATE TABLE `{case.table_name}`")
    status(stubber, statement_type="UTILITY")
    results(stubber, [[case.create_sql]], [{"Name": "createtab_stmt", "Type": "varchar"}], header=False)
    aliases = (
        "id_value",
        "mixed_value",
        "special_value",
        "nullable_value",
        "event_date_value",
        "event_ts_value",
        "amount_value",
        "bool_value",
    )
    start(stubber, case.qualified_select_sql)
    status(stubber)
    results(
        stubber,
        [[1, "Alpha", "S-1", None, "2024-02-03", "2024-02-03 04:05:06", "123.45", "true"]],
        [{"Name": name, "Type": kind} for name, kind in zip(aliases, types, strict=True)],
    )
    start(stubber, case.limit_sql)
    status(stubber)
    results(stubber, [[1]], [{"Name": "id_value", "Type": "bigint"}])
    start(stubber, case.drop_sql)
    status(stubber, statement_type="DDL")
    assert_table_contract(connector, case)


@pytest.mark.parametrize("location", ["s3://bucket", "s3://bucket/", "s3://:secret@bucket/contracts"])
def test_writable_location_requires_a_safe_dedicated_prefix(location):
    with pytest.raises(ValueError):
        writable_location(location, "contract_unit")


def test_writable_location_is_a_child_of_the_supplied_prefix():
    assert writable_location("s3://fixture-bucket/contracts/", "contract_unit") == (
        "fixture-bucket",
        "contracts/contract_unit/",
    )


@pytest.mark.parametrize("denied", [False, True])
def test_contract_data_cleanup_is_scoped_paginated_and_surfaces_delete_errors(denied):
    bucket, prefix = "fixture-bucket", "contracts/contract_unit/"
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test")
    try:
        with Stubber(client) as stubber:
            stubber.add_response(
                "list_objects_v2",
                {"Contents": [{"Key": prefix + "one"}], "IsTruncated": not denied, "NextContinuationToken": "next"},
                {"Bucket": bucket, "Prefix": prefix},
            )
            response = {"Errors": [{"Key": prefix + "one", "Code": "AccessDenied"}]} if denied else {}
            stubber.add_response(
                "delete_objects",
                response,
                {"Bucket": bucket, "Delete": {"Objects": [{"Key": prefix + "one"}], "Quiet": True}},
            )
            if denied:
                with pytest.raises(RuntimeError, match="AccessDenied"):
                    delete_contract_data(client, bucket, prefix)
            else:
                stubber.add_response(
                    "list_objects_v2",
                    {"Contents": [{"Key": prefix + "two"}], "IsTruncated": False},
                    {"Bucket": bucket, "Prefix": prefix, "ContinuationToken": "next"},
                )
                stubber.add_response(
                    "delete_objects",
                    {},
                    {"Bucket": bucket, "Delete": {"Objects": [{"Key": prefix + "two"}], "Quiet": True}},
                )
                delete_contract_data(client, bucket, prefix)
            stubber.assert_no_pending_responses()
    finally:
        client.close()
