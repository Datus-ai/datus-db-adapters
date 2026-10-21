# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Connection-local support for Inceptor's HiveServer2 result protocol.

The type IDs come from TDH CE 2024.5's inceptor-service-8.37.3.jar,
org.apache.hive.service.cli.thrift.TTypeId. IDs 18 through 21 conflict with
Apache Hive, and IDs 22 through 26 extend it. Never modify PyHive's globals.
"""

import sys

from pyhive import hive
from pyhive.common import DBAPITypeObject
from TCLIService import TCLIService, ttypes
from thrift.protocol.TBase import TBase
from thrift.protocol.TBinaryProtocol import TBinaryProtocol
from thrift.Thrift import TApplicationException, TMessageType, TType

_TYPE_NAMES = {key: value for key, value in ttypes.TTypeId._VALUES_TO_NAMES.items() if key < 18}
_TYPE_NAMES.update(
    {
        18: "INTERVAL_YEAR_TO_MONTH_TYPE",
        19: "INTERVAL_DAY_TO_SECOND_TYPE",
        20: "VARCHAR_TYPE",
        21: "CHAR_TYPE",
        22: "CURSOR_TYPE",
        23: "TIME_TYPE",
        24: "VARCHAR2_TYPE",
        25: "GEO_TYPE",
        26: "JSON_TYPE",
    }
)
_TYPE_OBJECTS = {name: DBAPITypeObject([name]) for name in _TYPE_NAMES.values() if not hasattr(hive, name)}


def _struct(cls):
    return cls, cls.thrift_spec


def _replace_field(spec, name, field_type, args):
    return tuple(
        (field[0], field_type, name, args, field[4]) if field and field[2] == name else field for field in spec
    )


class _ColumnValue(ttypes.TColumnValue):
    # Inceptor adds an operation handle for a REF CURSOR as field 8. The
    # generated Apache class otherwise silently skips it during deserialization.
    thrift_spec = ttypes.TColumnValue.thrift_spec + (
        (8, TType.STRUCT, "handleVal", _struct(ttypes.TOperationHandle), None),
    )
    read = TBase.read
    write = TBase.write

    def __init__(self, *args, handleVal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.handleVal = handleVal


class _Row(ttypes.TRow):
    thrift_spec = _replace_field(
        ttypes.TRow.thrift_spec, "colVals", TType.LIST, (TType.STRUCT, _struct(_ColumnValue), False)
    )
    read = TBase.read
    write = TBase.write


class _RowSet(ttypes.TRowSet):
    thrift_spec = _replace_field(ttypes.TRowSet.thrift_spec, "rows", TType.LIST, (TType.STRUCT, _struct(_Row), False))
    read = TBase.read
    write = TBase.write


class _FetchResponse(ttypes.TFetchResultsResp):
    thrift_spec = _replace_field(ttypes.TFetchResultsResp.thrift_spec, "results", TType.STRUCT, _struct(_RowSet))
    read = TBase.read
    write = TBase.write


class _FetchResult(TCLIService.FetchResults_result):
    thrift_spec = _replace_field(
        TCLIService.FetchResults_result.thrift_spec, "success", TType.STRUCT, _struct(_FetchResponse)
    )
    read = TBase.read
    write = TBase.write


class _Client(TCLIService.Client):
    def recv_FetchResults(self):
        _, message_type, _ = self._iprot.readMessageBegin()
        if message_type == TMessageType.EXCEPTION:
            error = TApplicationException()
            error.read(self._iprot)
            self._iprot.readMessageEnd()
            raise error
        result = _FetchResult()
        # PyHive's legacy generated specs use tuples where newer accelerated
        # Thrift codecs require lists. The portable binary reader accepts both.
        result.read(TBinaryProtocol(self._iprot.trans))
        self._iprot.readMessageEnd()
        if result.success is None:
            raise TApplicationException(TApplicationException.MISSING_RESULT, "FetchResults failed: missing response")
        return result.success


class InceptorCursor(hive.Cursor):
    @property
    def description(self):
        if self._operationHandle is None or not self._operationHandle.hasResultSet:
            return None
        if self._description is None:
            response = self._connection.client.GetResultSetMetadata(
                ttypes.TGetResultSetMetadataReq(self._operationHandle)
            )
            hive._check_status(response)
            description = []
            for column in response.schema.columns:
                if not column.typeDesc or not column.typeDesc.types:
                    raise hive.DataError(f"Missing Inceptor result type for column {column.columnName!r}")
                primitive = column.typeDesc.types[0].primitiveEntry
                type_name = "STRING_TYPE" if primitive is None else _TYPE_NAMES.get(primitive.type)
                if type_name is None:
                    raise hive.NotSupportedError(
                        f"Unsupported Inceptor result type ID {primitive.type} for column {column.columnName!r}"
                    )
                description.append((column.columnName, type_name, None, None, None, None, True))
            self._description = description
        return self._description

    def _fetch_more(self):
        if self._operationHandle is None or not self._operationHandle.hasResultSet:
            raise hive.ProgrammingError("No result set")
        response = self._connection.client.FetchResults(
            ttypes.TFetchResultsReq(
                operationHandle=self._operationHandle,
                orientation=ttypes.TFetchOrientation.FETCH_NEXT,
                maxRows=self.arraysize,
            )
        )
        hive._check_status(response)
        schema = self.description
        result = response.results
        if result.rows:
            data = []
            for row in result.rows:
                if len(row.colVals) != len(schema):
                    raise hive.DataError("Inceptor result row does not match its column metadata")
                data.append(tuple(self._row_value(value, column[1]) for value, column in zip(row.colVals, schema)))
        elif result.columns:
            if len(result.columns) != len(schema):
                raise hive.DataError("Inceptor result columns do not match their metadata")
            columns = [hive._unwrap_column(column, meta[1]) for column, meta in zip(result.columns, schema)]
            if len({len(column) for column in columns}) != 1:
                raise hive.DataError("Inceptor result columns have inconsistent row counts")
            data = list(zip(*columns))
        else:
            data = []
        self._data += data
        if not data:
            self._state = self._STATE_FINISHED

    def _row_value(self, cell, type_name):
        if type_name == "CURSOR_TYPE":
            handle = getattr(cell, "handleVal", None)
            if handle is None:
                return None
            nested = type(self)(self._connection, arraysize=self.arraysize)
            nested._operationHandle = handle
            nested._state = nested._STATE_RUNNING
            try:
                names = [column[0] for column in nested.description] if handle.hasResultSet else []
                return [dict(zip(names, row)) for row in nested.fetchall()] if handle.hasResultSet else []
            finally:
                original_error = sys.exception()
                try:
                    nested.close()
                except Exception as close_error:
                    if original_error is None:
                        raise
                    original_error.add_note(f"Nested Inceptor cursor cleanup failed: {close_error}")
        for name in ("boolVal", "byteVal", "i16Val", "i32Val", "i64Val", "doubleVal", "stringVal"):
            wrapper = getattr(cell, name)
            if wrapper is not None:
                value = wrapper.value
                converter = hive.TYPES_CONVERTER.get(type_name)
                return converter(value) if value is not None and converter else value
        raise hive.DataError(f"Missing Inceptor row value for {type_name}")


class InceptorConnection(hive.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        client = self._client
        self._client = _Client(client._iprot, client._oprot)
        self._client._seqid = client._seqid

    def cursor(self, *args, **kwargs):
        return InceptorCursor(self, *args, **kwargs)


class _DBAPI:
    """Reuse PyHive's DBAPI contract with a connection-local cursor/client."""

    def connect(self, *args, **kwargs):
        return InceptorConnection(*args, **kwargs)

    def __getattr__(self, name):
        if name in _TYPE_OBJECTS:
            return _TYPE_OBJECTS[name]
        return getattr(hive, name)


dbapi = _DBAPI()
