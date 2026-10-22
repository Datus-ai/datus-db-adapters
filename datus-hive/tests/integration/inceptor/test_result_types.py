# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Real Inceptor result types, including IDs that differ from Apache Hive."""

import csv
import io
import json

import pytest
from TCLIService import ttypes

from datus_db_core.testing import contract

from ._cursor_protocol import cursor_out_parameter

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "expression,type_name,expected",
    [
        ("CAST('中文' AS VARCHAR2(20))", "VARCHAR2_TYPE", "中文"),
        ("CAST('可变字符串' AS VARCHAR(20))", "VARCHAR_TYPE", "可变字符串"),
        ("CAST('fixed' AS CHAR(8))", "CHAR_TYPE", "fixed   "),
        ("CAST('10:07:05' AS TIME)", "TIME_TYPE", "10:07:05"),
        ("INTERVAL '1-2' YEAR TO MONTH", "INTERVAL_YEAR_TO_MONTH_TYPE", "1-2"),
        ("INTERVAL '2 03:04:05.006' DAY TO SECOND", "INTERVAL_DAY_TO_SECOND_TYPE", "2 03:04:05.006000000"),
        ("CAST('POINT (1 2)' AS GEO)", "GEO_TYPE", "POINT (1 2)"),
    ],
)
def test_inceptor_scalar_type_round_trip(connector, expression, type_name, expected):
    # Check actual DBAPI metadata as well as the public adapter's returned value.
    with connector._ensure_engine().connect() as connection:
        cursor = connection.connection.dbapi_connection.cursor()
        try:
            cursor.execute(f"SELECT {expression} AS value")
            assert cursor.description[0][1] == type_name
            assert cursor.fetchall() == [(expected,)]
        finally:
            cursor.close()
    result = connector.execute_query(f"SELECT {expression} AS value", result_format="list")
    contract.assert_success(result, expression)
    assert result.sql_return == [{"value": expected}]


@pytest.mark.parametrize(
    "payload,expected",
    [
        ('{"nested":[1,null,"json"]}', {"nested": [1, None, "json"]}),
        (
            '{"customer_id":10,"tags":["vip",null],"active":true}',
            {"customer_id": 10, "tags": ["vip", None], "active": True},
        ),
        ('{"inactive":false,"empty":null,"amount":-10.5}', {"inactive": False, "empty": None, "amount": -10.5}),
        ('{"note":"50% / %s / %(name)s / :name"}', {"note": "50% / %s / %(name)s / :name"}),
    ],
)
@pytest.mark.parametrize("result_format", ["list", "csv", "pandas", "arrow"])
def test_inceptor_json_payload(connector, payload, expected, result_format):
    # CE 2024.5 replaces non-ASCII JSON strings before writing its HS2 response.
    # UTF-8 preservation at the driver boundary is covered by the wire tests;
    # use a server-supported JSON value for this engine round trip.
    expression = f"JSON_PARSE('{payload}')"
    with connector._ensure_engine().connect() as connection:
        cursor = connection.connection.dbapi_connection.cursor()
        try:
            cursor.execute(f"SELECT {expression} AS value")
            assert cursor.description[0][1] == "JSON_TYPE"
            assert json.loads(cursor.fetchall()[0][0]) == expected
        finally:
            cursor.close()
    result = connector.execute_query(f"SELECT {expression} AS value", result_format=result_format)
    contract.assert_success(result, "JSON result")
    assert result.row_count == 1
    if result_format == "list":
        rows = result.sql_return
    elif result_format == "csv":
        rows = list(csv.DictReader(io.StringIO(result.sql_return)))
    elif result_format == "pandas":
        rows = result.sql_return.to_dict(orient="records")
    else:
        rows = result.sql_return.to_pylist()
    assert len(rows) == 1
    assert json.loads(rows[0]["value"]) == expected


def test_system_varchar2_and_aggregate_queries(connector):
    """System column names are VARCHAR2; COUNT has a separate BIGINT result."""
    condition = "database_name = 'system' AND table_name = 'columns_v'"
    result = connector.execute_query(
        f"SELECT column_name AS name FROM system.columns_v WHERE {condition}", result_format="list"
    )
    contract.assert_success(result, "system VARCHAR2 column names")
    names = [row["name"] for row in result.sql_return]
    assert "column_name" in names
    for projection, expected in [
        ("COUNT(*)", len(names)),
        ("COUNT(DISTINCT column_name)", len(set(names))),
    ]:
        result = connector.execute_query(
            f"SELECT {projection} AS cnt FROM system.columns_v WHERE {condition}",
            result_format="list",
        )
        contract.assert_success(result, projection)
        assert result.sql_return == [{"cnt": expected}]
    result = connector.execute_query(
        f"SELECT MIN(column_name) AS min_name, MAX(column_name) AS max_name FROM system.columns_v WHERE {condition}",
        result_format="list",
    )
    contract.assert_success(result, "VARCHAR2 MIN/MAX")
    assert result.sql_return == [{"min_name": min(names), "max_name": max(names)}]


@pytest.mark.parametrize("result_format", ["list", "csv", "pandas", "arrow"])
def test_varchar2_result_formats(connector, result_format):
    result = connector.execute_query("SELECT CAST('中文' AS VARCHAR2(20)) AS value", result_format=result_format)
    contract.assert_success(result, result_format)
    if result_format == "list":
        assert result.sql_return == [{"value": "中文"}]
    elif result_format == "csv":
        assert result.sql_return.strip() == "value\n中文"
    elif result_format == "pandas":
        assert result.sql_return.to_dict(orient="records") == [{"value": "中文"}]
    else:
        assert result.sql_return.to_pylist() == [{"value": "中文"}]


def test_native_cursor_out_parameter(connector, objects):
    database, _, _, table_ref, _ = objects
    procedure = f"`{database}`.`get_cursor_rows`"
    contract.assert_success(
        connector.execute_ddl(
            f"CREATE OR REPLACE PROCEDURE {procedure}(rc OUT SYS_REFCURSOR) AS BEGIN "
            f"OPEN rc FOR SELECT id, CAST(label AS VARCHAR2(20)) AS label FROM {table_ref}; END;"
        ),
        "create native cursor procedure",
    )
    with connector._ensure_engine().connect() as connection:
        driver = connection.connection.dbapi_connection
        try:
            with cursor_out_parameter(driver, f"CALL {procedure}(?)") as parameters:
                assert len(parameters) == 1
                parameter = parameters[0]
                assert parameter.type == 22  # Native CURSOR_TYPE, not a text representation.
                cursor = driver.cursor()
                try:
                    assert cursor._row_value(parameter.value, "CURSOR_TYPE") == [{"id": 1, "label": "本地联调"}]
                finally:
                    cursor.close()
                status = driver.client.GetOperationStatus(ttypes.TGetOperationStatusReq(parameter.value.handleVal))
                assert status.status.statusCode in {
                    ttypes.TStatusCode.ERROR_STATUS,
                    ttypes.TStatusCode.INVALID_HANDLE_STATUS,
                }, "Materializing a native cursor must close its operation handle"
        except Exception:
            if not driver._transport.isOpen():
                connection.invalidate()
            raise
