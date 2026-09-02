# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""PostgreSQL-dialect TPC-H test data definitions.

Table names are unqualified: callers set the target schema through the
connector's ``schema_name`` (applied as ``SET search_path`` on every
statement), matching the Greenplum and GaussDB adapters.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE IF NOT EXISTS "tpch_region" (
        "regionkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("regionkey")
    )
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE IF NOT EXISTS "tpch_nation" (
        "nationkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "regionkey" INTEGER NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("nationkey")
    )
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE IF NOT EXISTS "tpch_customer" (
        "custkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        "mktsegment" VARCHAR(10) NOT NULL,
        PRIMARY KEY ("custkey")
    )
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE IF NOT EXISTS "tpch_orders" (
        "orderkey" INTEGER NOT NULL,
        "custkey" INTEGER NOT NULL,
        "orderstatus" CHAR(1) NOT NULL,
        "totalprice" DECIMAL(15,2) NOT NULL,
        "orderdate" DATE NOT NULL,
        PRIMARY KEY ("orderkey")
    )
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE IF NOT EXISTS "tpch_supplier" (
        "suppkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        PRIMARY KEY ("suppkey")
    )
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("PostgreSQL TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA = build_tpch_inserts(lambda t: f'"{t}"')

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
