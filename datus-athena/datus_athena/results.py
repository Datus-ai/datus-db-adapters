# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Convert Athena's string-valued API results using declared column types."""

import re
from datetime import date, datetime
from decimal import Decimal

import pyarrow as pa


def arrow_type(column: dict) -> pa.DataType:
    name = column["Type"].lower()
    scalar = {
        "boolean": pa.bool_(),
        "tinyint": pa.int8(),
        "smallint": pa.int16(),
        "integer": pa.int32(),
        "int": pa.int32(),
        "bigint": pa.int64(),
        "float": pa.float32(),
        "real": pa.float32(),
        "double": pa.float64(),
        "date": pa.date32(),
        "timestamp": pa.timestamp("us"),
        "varbinary": pa.binary(),
        "binary": pa.binary(),
    }
    if name in scalar:
        return scalar[name]
    if name.startswith("decimal"):
        match = re.fullmatch(r"decimal\((\d+),\s*(\d+)\)", name)
        precision = int(match[1]) if match else column.get("Precision", 38)
        scale = int(match[2]) if match else column.get("Scale", 0)
        return pa.decimal128(precision, scale)
    # Complex types and time-zone/high-precision timestamps stay lossless text.
    return pa.string()


def convert_value(value: str | None, dtype: pa.DataType):
    if value is None:
        return None
    if pa.types.is_boolean(dtype):
        if value.lower() not in {"true", "false"}:
            raise ValueError(f"Invalid Athena boolean: {value!r}")
        return value.lower() == "true"
    if pa.types.is_integer(dtype):
        return int(value)
    if pa.types.is_floating(dtype):
        return float(value)
    if pa.types.is_decimal(dtype):
        return Decimal(value)
    if pa.types.is_date(dtype):
        return date.fromisoformat(value)
    if pa.types.is_timestamp(dtype):
        fraction = re.search(r"\.(\d+)", value)
        if fraction and len(fraction[1]) > 6:
            raise ValueError("Timestamp exceeds microsecond precision; CAST it to VARCHAR to preserve all digits")
        return datetime.fromisoformat(value)
    if pa.types.is_binary(dtype):
        return bytes.fromhex(value)
    return value
