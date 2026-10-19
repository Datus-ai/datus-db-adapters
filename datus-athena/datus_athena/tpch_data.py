# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Athena CTAS statements built from the repository's shared tiny TPC-H data."""

import sqlglot

from datus_db_core.testing.tpch import TPCH_TABLES, build_tpch_inserts

TPCH_COLUMNS = [
    [("r_regionkey", "BIGINT"), ("r_name", "VARCHAR"), ("r_comment", "VARCHAR")],
    [("n_nationkey", "BIGINT"), ("n_name", "VARCHAR"), ("n_regionkey", "BIGINT"), ("n_comment", "VARCHAR")],
    [
        ("c_custkey", "BIGINT"),
        ("c_name", "VARCHAR"),
        ("c_nationkey", "BIGINT"),
        ("c_acctbal", "DECIMAL(15,2)"),
        ("c_mktsegment", "VARCHAR"),
    ],
    [
        ("o_orderkey", "BIGINT"),
        ("o_custkey", "BIGINT"),
        ("o_orderstatus", "VARCHAR"),
        ("o_totalprice", "DECIMAL(15,2)"),
        ("o_orderdate", "DATE"),
    ],
    [("s_suppkey", "BIGINT"), ("s_name", "VARCHAR"), ("s_nationkey", "BIGINT"), ("s_acctbal", "DECIMAL(15,2)")],
]


def build_tpch_ctas(data_location: str, database: str) -> list[str]:
    from .config import AthenaConfig
    from .connector import AthenaConnector

    AthenaConfig(output_location=data_location)
    from urllib.parse import urlsplit

    if not urlsplit(data_location).path.strip("/"):
        raise ValueError("TPC-H data_location must use a dedicated non-root S3 prefix")
    quote = AthenaConnector.quote_identifier
    statements = []
    for table, columns, insert in zip(TPCH_TABLES, TPCH_COLUMNS, build_tpch_inserts(), strict=True):
        values = sqlglot.parse_one(insert, read="athena").expression.sql(dialect="athena")
        fields = ", ".join(f"CAST({quote(name)} AS {kind}) AS {quote(name)}" for name, kind in columns)
        aliases = ", ".join(quote(name) for name, _ in columns)
        location = (data_location.rstrip("/") + "/" + table + "/").replace("'", "''")
        statements.append(
            f"CREATE TABLE {quote(database)}.{quote(table)} "
            f"WITH (format='PARQUET', external_location='{location}') AS "
            f"SELECT {fields} FROM ({values}) AS fixture ({aliases})"
        )
    return statements
