# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Redshift-dialect TPC-H test data definitions.

Statements are schema-templated: every DDL and INSERT contains a literal
``{schema}`` placeholder that callers fill via ``.format(schema=...)``.
Redshift has no session-level ``search_path`` handling in this adapter, so
tables are always addressed as ``<schema>.<table>``.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE IF NOT EXISTS {schema}.tpch_region (
        regionkey INTEGER,
        name VARCHAR(25),
        comment VARCHAR(152)
    )
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE IF NOT EXISTS {schema}.tpch_nation (
        nationkey INTEGER,
        name VARCHAR(25),
        regionkey INTEGER,
        comment VARCHAR(152)
    )
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE IF NOT EXISTS {schema}.tpch_customer (
        custkey INTEGER,
        name VARCHAR(25),
        nationkey INTEGER,
        acctbal DECIMAL(15,2),
        mktsegment VARCHAR(10)
    )
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE IF NOT EXISTS {schema}.tpch_orders (
        orderkey INTEGER,
        custkey INTEGER,
        orderstatus VARCHAR(1),
        totalprice DECIMAL(15,2),
        orderdate DATE
    )
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE IF NOT EXISTS {schema}.tpch_supplier (
        suppkey INTEGER,
        name VARCHAR(25),
        nationkey INTEGER,
        acctbal DECIMAL(15,2)
    )
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("Redshift TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA = build_tpch_inserts(lambda t: f"{{schema}}.{t}")

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
