# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Vendor prepared-call RPCs used to produce a real SYS_REFCURSOR fixture.

These additional messages are absent from PyHive. Field definitions come from
TDH CE 2024.5's inceptor-service-8.37.3.jar; no generated SDK globals are changed.
"""

import sys
from contextlib import contextmanager

from pyhive import hive
from TCLIService import ttypes
from thrift.protocol.TBase import TBase
from thrift.protocol.TBinaryProtocol import TBinaryProtocol
from thrift.Thrift import TApplicationException, TMessageType, TType

from datus_hive.inceptor import _ColumnValue


def _struct(cls):
    return cls, cls.thrift_spec


class _Message(TBase):
    def __init__(self, **values):
        for field in self.thrift_spec:
            if field:
                setattr(self, field[2], values.get(field[2]))


class _Param(_Message):
    thrift_spec = (
        None,
        (1, TType.I32, "type", None, None),
        (2, TType.STRING, "name", "UTF8", None),
        (3, TType.STRUCT, "value", _struct(_ColumnValue), None),
        (4, TType.I32, "attrInOut", None, None),
        (5, TType.I32, "attrCopy", None, None),
    )


class _ExecuteRequest(_Message):
    # Vendor field 5 is a parameter list; Apache Hive uses it for queryTimeout.
    thrift_spec = ttypes.TExecuteStatementReq.thrift_spec[:5] + (
        (5, TType.LIST, "parameters", (TType.STRUCT, _struct(_Param), False), None),
    )


class _ExecuteResponse(_Message):
    # Decode OUT parameters explicitly. Skipping this unknown field with the
    # Apache response class can try to decode binary handle bytes as UTF-8.
    thrift_spec = ttypes.TExecuteStatementResp.thrift_spec + (
        (3, TType.LIST, "parameters", (TType.STRUCT, _struct(_Param), False), None),
    )


class _PreCompileRequest(_Message):
    thrift_spec = (None, (1, TType.STRUCT, "execStmtReq", _struct(_ExecuteRequest), None))


class _PreparedRequest(_Message):
    thrift_spec = (
        None,
        (1, TType.STRUCT, "operationHandle", _struct(ttypes.TOperationHandle), None),
        (2, TType.STRUCT, "execStmtReq", _struct(_ExecuteRequest), None),
    )


class _Response(_Message):
    thrift_spec = (None, (1, TType.STRUCT, "execStmtResp", _struct(_ExecuteResponse), None))


class _Result(_Message):
    thrift_spec = ((0, TType.STRUCT, "success", _struct(_Response), None),)


def _rpc(client, method, request):
    output = client._oprot
    output.writeMessageBegin(method, TMessageType.CALL, client._seqid)
    output.writeStructBegin("args")
    output.writeFieldBegin("req", TType.STRUCT, 1)
    request.write(output)
    output.writeFieldEnd()
    output.writeFieldStop()
    output.writeStructEnd()
    output.writeMessageEnd()
    output.trans.flush()
    try:
        _, message_type, _ = client._iprot.readMessageBegin()
        if message_type == TMessageType.EXCEPTION:
            error = TApplicationException()
            error.read(client._iprot)
            client._iprot.readMessageEnd()
            raise error
        result = _Result()
        result.read(TBinaryProtocol(client._iprot.trans))
        client._iprot.readMessageEnd()
        if result.success is None:
            raise TApplicationException(TApplicationException.MISSING_RESULT, f"{method}: missing response")
        return result.success.execStmtResp
    except Exception:
        # Never reuse a connection with a partially consumed RPC response in
        # fixture cleanup: a subsequent CloseSession can otherwise block.
        client._iprot.trans.close()
        raise


@contextmanager
def cursor_out_parameter(driver, statement):
    handles = []
    try:
        request = _ExecuteRequest(
            sessionHandle=driver.sessionHandle,
            statement=statement,
            confOverlay={},
            runAsync=True,
            parameters=[_Param(type=16, name="PARAM_1")],
        )
        compiled = _rpc(driver.client, "PreCompileStatement", _PreCompileRequest(execStmtReq=request))
        hive._check_status(compiled)
        handles.append(compiled.operationHandle)
        request.parameters = [_Param(type=22, name="PARAM_1", attrInOut=1)]
        response = _rpc(
            driver.client,
            "ExecutePreCompiledStatement",
            _PreparedRequest(operationHandle=compiled.operationHandle, execStmtReq=request),
        )
        hive._check_status(response)
        if response.operationHandle not in handles:
            handles.append(response.operationHandle)
        yield response.parameters
    finally:
        original_error = sys.exception()
        if driver._transport.isOpen():
            for handle in reversed(handles):
                if handle is not None:
                    try:
                        hive._check_status(driver.client.CloseOperation(ttypes.TCloseOperationReq(handle)))
                    except Exception as cleanup_error:
                        if original_error is None:
                            raise
                        original_error.add_note(f"Prepared cursor fixture cleanup failed: {cleanup_error}")
