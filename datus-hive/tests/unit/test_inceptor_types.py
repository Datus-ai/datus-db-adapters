# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Exercise real PyHive cursors and Thrift decoding at the RPC boundary."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyhive import hive
from TCLIService import TCLIService, ttypes
from thrift.protocol import TBinaryProtocol
from thrift.Thrift import TApplicationException, TMessageType
from thrift.transport import TTransport

from datus_hive import HiveConfig, HiveConnector
from datus_hive.inceptor import (
    InceptorConnection,
    InceptorCursor,
    _Client,
    _ColumnValue,
    _FetchResponse,
    _FetchResult,
    _Row,
    _RowSet,
    dbapi,
)

# Independently specified cases from the vendor's TTypeId enum; assert values,
# nulls and logical type names, rather than just comparing mapping dictionaries.
EXTENDED_TYPES = [
    (18, "INTERVAL_YEAR_TO_MONTH_TYPE", "-1-2"),
    (19, "INTERVAL_DAY_TO_SECOND_TYPE", "-2 03:04:05.006"),
    (20, "VARCHAR_TYPE", "可变字符串"),
    (21, "CHAR_TYPE", "fixed   "),
    (23, "TIME_TYPE", "24:00:00"),
    (24, "VARCHAR2_TYPE", "中文 VARCHAR2"),
    (25, "GEO_TYPE", "POINT (1 2)"),
    (26, "JSON_TYPE", '{"nested":[1,null,"中文"]}'),
]


def status():
    return ttypes.TStatus(statusCode=ttypes.TStatusCode.SUCCESS_STATUS)


def handle(name=b"parent"):
    return ttypes.TOperationHandle(
        operationId=ttypes.THandleIdentifier(guid=name, secret=b"test-secret"),
        operationType=ttypes.TOperationType.EXECUTE_STATEMENT,
        hasResultSet=True,
    )


def metadata(*columns):
    return ttypes.TGetResultSetMetadataResp(
        status=status(),
        schema=ttypes.TTableSchema(
            columns=[
                ttypes.TColumnDesc(
                    columnName=name,
                    position=index,
                    typeDesc=ttypes.TTypeDesc(
                        types=[ttypes.TTypeEntry(primitiveEntry=ttypes.TPrimitiveTypeEntry(type=type_id))]
                    ),
                )
                for index, (name, type_id) in enumerate(columns)
            ]
        ),
    )


def cursor_for(response, pages, cursor_class=InceptorCursor):
    client = MagicMock(spec=TCLIService.Client)
    client.GetResultSetMetadata.return_value = response
    client.FetchResults.side_effect = pages
    client.CloseOperation.return_value = ttypes.TCloseOperationResp(status=status())
    cursor = cursor_class(SimpleNamespace(client=client), arraysize=2)
    cursor._operationHandle = handle()
    cursor._state = cursor._STATE_RUNNING
    return cursor, client


def page(columns=None, rows=None):
    return _FetchResponse(status=status(), results=_RowSet(rows=rows or [], columns=columns or []))


@pytest.mark.parametrize("type_id,type_name,value", EXTENDED_TYPES)
@pytest.mark.parametrize("row_format", [False, True], ids=["columnar", "row-based"])
def test_extension_values_and_nulls(type_id, type_name, value, row_format):
    if row_format:
        data = page(
            rows=[
                _Row(colVals=[_ColumnValue(stringVal=ttypes.TStringValue(value=value))]),
                _Row(colVals=[_ColumnValue(stringVal=ttypes.TStringValue())]),
            ]
        )
    else:
        data = page(columns=[ttypes.TColumn(stringVal=ttypes.TStringColumn(values=[value, ""], nulls=b"\x02"))])
    cursor, client = cursor_for(metadata(("value", type_id)), [data, page()])
    try:
        assert cursor.description == [("value", type_name, None, None, None, None, True)]
        assert cursor.fetchall() == [(value,), (None,)]
        client.GetResultSetMetadata.assert_called_once()
        assert [call.args[0].maxRows for call in client.FetchResults.call_args_list] == [2, 2]
    finally:
        cursor.close()


@pytest.mark.parametrize(
    "type_id,hive_name,inceptor_name",
    [
        (18, "VARCHAR_TYPE", "INTERVAL_YEAR_TO_MONTH_TYPE"),
        (19, "CHAR_TYPE", "INTERVAL_DAY_TO_SECOND_TYPE"),
        (20, "INTERVAL_YEAR_MONTH_TYPE", "VARCHAR_TYPE"),
        (21, "INTERVAL_DAY_TIME_TYPE", "CHAR_TYPE"),
    ],
)
def test_hive_and_inceptor_connections_keep_distinct_type_meanings(type_id, hive_name, inceptor_name):
    original = dict(ttypes.TTypeId._VALUES_TO_NAMES)
    for cursor_class, expected in [(hive.Cursor, hive_name), (InceptorCursor, inceptor_name)]:
        cursor, _ = cursor_for(metadata(("value", type_id)), [], cursor_class)
        try:
            assert cursor.description[0][1] == expected
            assert ttypes.TTypeId._VALUES_TO_NAMES == original
        finally:
            cursor.close()


@pytest.mark.parametrize("row_format", [False, True])
def test_standard_types_still_decode(row_format):
    cells = [
        ttypes.TI32Value(value=0),
        ttypes.TBoolValue(value=False),
        ttypes.TStringValue(value="123.45"),
        ttypes.TStringValue(value="2024-02-03 04:05:06.123"),
        ttypes.TStringValue(value=""),
    ]
    if row_format:
        row = _Row(
            colVals=[
                _ColumnValue(i32Val=cells[0]),
                _ColumnValue(boolVal=cells[1]),
                _ColumnValue(stringVal=cells[2]),
                _ColumnValue(stringVal=cells[3]),
                _ColumnValue(stringVal=cells[4]),
            ]
        )
        data = page(rows=[row])
    else:
        data = page(
            columns=[
                ttypes.TColumn(i32Val=ttypes.TI32Column(values=[0], nulls=b"")),
                ttypes.TColumn(boolVal=ttypes.TBoolColumn(values=[False], nulls=b"")),
                *[ttypes.TColumn(stringVal=ttypes.TStringColumn(values=[c.value], nulls=b"")) for c in cells[2:]],
            ]
        )
    cursor, _ = cursor_for(metadata(("id", 3), ("flag", 0), ("amount", 15), ("ts", 8), ("empty", 7)), [data, page()])
    try:
        assert cursor.fetchall() == [(0, False, Decimal("123.45"), datetime(2024, 2, 3, 4, 5, 6, 123000), "")]
    finally:
        cursor.close()


def test_geometry_binary_payload_is_preserved():
    value = b"\x00\x01\xff"
    cursor, _ = cursor_for(
        metadata(("geo", 25)),
        [page(columns=[ttypes.TColumn(binaryVal=ttypes.TBinaryColumn(values=[value], nulls=b""))]), page()],
    )
    try:
        assert cursor.fetchall() == [(value,)]
    finally:
        cursor.close()


def test_unknown_type_has_column_context_and_does_not_cache_partial_schema():
    cursor, client = cursor_for(metadata(("known", 3), ("new_type", 99)), [])
    try:
        for _ in range(2):
            with pytest.raises(hive.NotSupportedError, match="type ID 99 for column 'new_type'"):
                _ = cursor.description
        assert cursor._description is None
        assert client.GetResultSetMetadata.call_count == 2
    finally:
        cursor.close()


def test_missing_and_complex_metadata():
    response = metadata(("struct", 12))
    response.schema.columns[0].typeDesc.types[0] = ttypes.TTypeEntry(
        structEntry=ttypes.TStructTypeEntry(nameToTypePtr={})
    )
    cursor, client = cursor_for(response, [])
    assert cursor.description[0][1] == "STRING_TYPE"
    cursor._description = None
    response.schema.columns[0].typeDesc.types = []
    with pytest.raises(hive.DataError, match="Missing Inceptor result type for column 'struct'"):
        _ = cursor.description
    cursor.close()


def test_no_result_set():
    cursor = InceptorCursor(SimpleNamespace(client=MagicMock()))
    assert cursor.description is None
    with pytest.raises(hive.ProgrammingError, match="No result set"):
        cursor._fetch_more()


@pytest.mark.parametrize("row_format", [False, True])
def test_mismatched_row_width_is_not_silently_truncated(row_format):
    data = (
        page(rows=[_Row(colVals=[])])
        if row_format
        else page(
            columns=[
                ttypes.TColumn(stringVal=ttypes.TStringColumn(values=["x"], nulls=b"")),
                ttypes.TColumn(stringVal=ttypes.TStringColumn(values=["y"], nulls=b"")),
            ]
        )
    )
    cursor, _ = cursor_for(metadata(("value", 24)), [data])
    with pytest.raises(hive.DataError, match="do not match|does not match"):
        cursor.fetchall()
    cursor.close()


def test_nested_cursor_is_materialized_and_closed():
    parent, child = handle(), handle(b"child")
    outer = page(rows=[_Row(colVals=[_ColumnValue(handleVal=child)]), _Row(colVals=[_ColumnValue()])])
    inner = page(columns=[ttypes.TColumn(stringVal=ttypes.TStringColumn(values=["中文"], nulls=b""))])
    cursor, client = cursor_for(metadata(("nested", 22)), [outer, inner, page(), page()])
    client.GetResultSetMetadata.side_effect = [metadata(("nested", 22)), metadata(("label", 24))]
    assert cursor.description[0][1] == "CURSOR_TYPE"
    assert cursor.fetchall() == [([{"label": "中文"}],), (None,)]
    assert client.CloseOperation.call_args.args[0].operationHandle == child
    cursor.close()
    assert client.CloseOperation.call_args.args[0].operationHandle == parent


def test_nested_cursor_closes_on_fetch_error():
    child = handle(b"child")
    cursor, client = cursor_for(
        metadata(("nested", 22)),
        [page(rows=[_Row(colVals=[_ColumnValue(handleVal=child)])]), hive.OperationalError("nested fetch failed")],
    )
    client.GetResultSetMetadata.side_effect = [metadata(("nested", 22)), metadata(("id", 3))]
    with pytest.raises(hive.OperationalError, match="nested fetch failed"):
        cursor.fetchall()
    assert client.CloseOperation.call_args.args[0].operationHandle == child
    cursor.close()


@pytest.mark.parametrize(
    "protocol_factory", [TBinaryProtocol.TBinaryProtocol, TBinaryProtocol.TBinaryProtocolAccelerated]
)
def test_cursor_handle_survives_actual_thrift_deserialization(protocol_factory):
    child = handle(b"child")
    # Generated independently with TDH CE 2024.5's inceptor-service-8.37.3.jar
    # and Java TBinaryProtocol: FetchResults_result containing one handleVal.
    # The handle identifiers are synthetic; no live credentials are included.
    wire = bytes.fromhex(
        "800100020000000c4665746368526573756c7473000000000c00000c00010800010000000000"
        "0c00030a000100000000000000000f00020c000000010f00010c000000010c00080c0001"
        "0b0001000000056368696c640b00020000000b746573742d7365637265740008000200000000"
        "02000301000000000000"
    )
    client = _Client(protocol_factory(TTransport.TMemoryBuffer(wire)))
    response = client.recv_FetchResults()
    assert response.results.rows[0].colVals[0].handleVal == child
    assert not hasattr(ttypes.TColumnValue(), "handleVal")


@pytest.mark.parametrize("exception", [False, True])
def test_fetch_rpc_errors(exception):
    transport = TTransport.TMemoryBuffer()
    output = TBinaryProtocol.TBinaryProtocol(transport)
    output.writeMessageBegin("FetchResults", TMessageType.EXCEPTION if exception else TMessageType.REPLY, 0)
    if exception:
        TApplicationException(TApplicationException.INTERNAL_ERROR, "server error").write(output)
    else:
        _FetchResult().write(output)
    output.writeMessageEnd()
    client = _Client(TBinaryProtocol.TBinaryProtocol(TTransport.TMemoryBuffer(transport.getvalue())))
    with pytest.raises(TApplicationException, match="server error|missing response"):
        client.recv_FetchResults()


def test_inceptor_dbapi_preserves_pyhive_contract():
    assert dbapi.paramstyle == hive.paramstyle
    assert dbapi.OperationalError is hive.OperationalError
    assert dbapi.INT_TYPE is hive.INT_TYPE
    assert dbapi.VARCHAR2_TYPE.values == (["VARCHAR2_TYPE"],)
    with pytest.raises(AttributeError):
        _ = dbapi.nonexistent_attribute


def test_inconsistent_column_lengths_are_not_silently_truncated():
    data = page(
        columns=[
            ttypes.TColumn(i32Val=ttypes.TI32Column(values=[1, 2], nulls=b"")),
            ttypes.TColumn(stringVal=ttypes.TStringColumn(values=["one"], nulls=b"")),
        ]
    )
    cursor, _ = cursor_for(metadata(("id", 3), ("value", 24)), [data])
    with pytest.raises(hive.DataError, match="inconsistent row counts"):
        cursor.fetchall()
    cursor.close()


def test_missing_row_value_reports_its_type():
    cursor, _ = cursor_for(metadata(("value", 24)), [page(rows=[_Row(colVals=[_ColumnValue()])])])
    with pytest.raises(hive.DataError, match="Missing Inceptor row value for VARCHAR2_TYPE"):
        cursor.fetchall()
    cursor.close()


@pytest.mark.parametrize("fetch_error", [False, True])
def test_nested_cursor_cleanup_failure_is_visible_without_masking_fetch_error(fetch_error):
    child = handle(b"child")
    pages = [page(rows=[_Row(colVals=[_ColumnValue(handleVal=child)])])]
    pages.append(hive.OperationalError("fetch failed") if fetch_error else page())
    cursor, client = cursor_for(metadata(("nested", 22)), pages)
    client.GetResultSetMetadata.side_effect = [metadata(("nested", 22)), metadata(("id", 3))]
    client.CloseOperation.side_effect = [
        hive.OperationalError("close failed"),
        ttypes.TCloseOperationResp(status=status()),
    ]
    with pytest.raises(hive.OperationalError, match="fetch failed" if fetch_error else "close failed") as error:
        cursor.fetchall()
    if fetch_error:
        assert error.value.__notes__ == ["Nested Inceptor cursor cleanup failed: close failed"]
    cursor.close()


def test_inceptor_connection_uses_its_own_client_and_cursor():
    client = TCLIService.Client(MagicMock())
    client._seqid = 7

    def initialized(connection, *args, **kwargs):
        connection._client = client

    with patch.object(hive.Connection, "__init__", initialized):
        connection = InceptorConnection(host="hs2.example.com")
    assert isinstance(connection.client, _Client)
    assert connection.client._seqid == 7
    assert isinstance(connection.cursor(), InceptorCursor)


@pytest.mark.parametrize(
    "server_type,driver_name",
    [("hive", "pyhive.hive.Connection"), ("inceptor", "datus_hive.inceptor.InceptorConnection")],
)
def test_protocol_selection_reaches_real_sqlalchemy_dbapi_boundary(server_type, driver_name):
    connector = HiveConnector(HiveConfig(host="hs2.example.com", username="hive", server_type=server_type))
    try:
        with patch(driver_name, side_effect=RuntimeError("driver boundary")) as driver:
            with pytest.raises(RuntimeError, match="driver boundary"):
                connector._ensure_engine().connect()
            args = driver.call_args.kwargs
            assert args["username"] == "hive"
            assert args["database"] == "default"
            transport = args["thrift_transport"]
            assert transport.mechanism == "PLAIN"
            assert transport._trans.host == "hs2.example.com"
            assert transport._trans._timeout == 30
    finally:
        connector.close()
