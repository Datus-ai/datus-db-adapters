# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Hologres-dialect TPC-H test data definitions.

Hologres-specific DDL: every table is column-oriented with an explicit
``distribution_key``, and ``tpch_orders`` additionally declares its DATE column
as the ``event_time_column`` (Hologres' segment-key equivalent).

Table names are unqualified: callers set the target schema through the
connector's ``schema_name``, which is applied as ``SET search_path``.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE "tpch_region" (
        "regionkey" INTEGER NOT NULL PRIMARY KEY,
        "name" TEXT NOT NULL,
        "comment" TEXT
    )
    WITH (orientation = 'column', distribution_key = 'regionkey')
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE "tpch_nation" (
        "nationkey" INTEGER NOT NULL PRIMARY KEY,
        "name" TEXT NOT NULL,
        "regionkey" INTEGER NOT NULL,
        "comment" TEXT
    )
    WITH (orientation = 'column', distribution_key = 'nationkey')
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE "tpch_customer" (
        "custkey" INTEGER NOT NULL PRIMARY KEY,
        "name" TEXT NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15, 2) NOT NULL,
        "mktsegment" TEXT NOT NULL
    )
    WITH (orientation = 'column', distribution_key = 'custkey')
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE "tpch_orders" (
        "orderkey" INTEGER NOT NULL PRIMARY KEY,
        "custkey" INTEGER NOT NULL,
        "orderstatus" TEXT NOT NULL,
        "totalprice" DECIMAL(15, 2) NOT NULL,
        "orderdate" DATE NOT NULL
    )
    WITH (
        orientation = 'column',
        distribution_key = 'orderkey',
        event_time_column = 'orderdate'
    )
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE "tpch_supplier" (
        "suppkey" INTEGER NOT NULL PRIMARY KEY,
        "name" TEXT NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15, 2) NOT NULL
    )
    WITH (orientation = 'column', distribution_key = 'suppkey')
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("Hologres TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA = build_tpch_inserts(lambda t: f'"{t}"')

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
