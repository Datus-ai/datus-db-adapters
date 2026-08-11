# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Pandas-to-Oracle type inference for transfer target tables.

Oracle 19c has no SQL BOOLEAN column type; Datus booleans are stored as
``NUMBER(1)`` with values 1/0.
"""

from typing import Any

_VARCHAR2_MAX_BYTES = 4000
_TEXT_TYPE = f"VARCHAR2({_VARCHAR2_MAX_BYTES})"


def infer_oracle_type(series: Any) -> str:
    """Return the Oracle 19c column type for a pandas ``series``."""
    from datetime import date, datetime, time
    from decimal import Decimal

    from pandas.api import types as pd_types

    dtype = series.dtype

    if pd_types.is_bool_dtype(dtype):
        return "NUMBER(1)"
    if pd_types.is_integer_dtype(dtype):
        return "NUMBER(19)"
    if pd_types.is_float_dtype(dtype):
        return "BINARY_DOUBLE"
    if pd_types.is_datetime64_any_dtype(dtype):
        if getattr(dtype, "tz", None) is not None:
            return "TIMESTAMP WITH TIME ZONE"
        return "TIMESTAMP"
    if pd_types.is_timedelta64_dtype(dtype):
        return _TEXT_TYPE

    # Object dtype: inspect the first non-null value
    sample = series.dropna()
    if len(sample) == 0:
        return _TEXT_TYPE
    value = sample.iloc[0]
    if isinstance(value, bool):
        return "NUMBER(1)"
    if isinstance(value, int):
        return "NUMBER(19)"
    if isinstance(value, float):
        return "BINARY_DOUBLE"
    if isinstance(value, Decimal):
        return "NUMBER(38,10)"
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, date):
        return "DATE"
    if isinstance(value, time):
        return _TEXT_TYPE
    if isinstance(value, bytes):
        return "BLOB"
    if isinstance(value, str):
        max_bytes = max(len(item.encode("utf-8")) for item in sample if isinstance(item, str))
        return _TEXT_TYPE if max_bytes <= _VARCHAR2_MAX_BYTES else "CLOB"
    return _TEXT_TYPE
