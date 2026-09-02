# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""DWS-dialect TPC-H test data definitions.

The rows come from the shared ``datus-db-core`` fixture. DWS-specific DDL
keeps every table row-oriented and gives it an explicit hash distribution key
so the fixture exercises native DWS table semantics instead of relying on the
cluster's default distribution policy.

Table names are unqualified: callers set the target schema through the
connector's ``schema_name``, which is applied as ``SET search_path``.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE "tpch_region" (
        "regionkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("regionkey")
    ) WITH (orientation=row) DISTRIBUTE BY HASH ("regionkey")
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE "tpch_nation" (
        "nationkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "regionkey" INTEGER NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("nationkey")
    ) WITH (orientation=row) DISTRIBUTE BY HASH ("nationkey")
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE "tpch_customer" (
        "custkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        "mktsegment" VARCHAR(10) NOT NULL,
        PRIMARY KEY ("custkey")
    ) WITH (orientation=row) DISTRIBUTE BY HASH ("custkey")
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE "tpch_orders" (
        "orderkey" INTEGER NOT NULL,
        "custkey" INTEGER NOT NULL,
        "orderstatus" CHAR(1) NOT NULL,
        "totalprice" DECIMAL(15,2) NOT NULL,
        "orderdate" DATE NOT NULL,
        PRIMARY KEY ("orderkey")
    ) WITH (orientation=row) DISTRIBUTE BY HASH ("orderkey")
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE "tpch_supplier" (
        "suppkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        PRIMARY KEY ("suppkey")
    ) WITH (orientation=row) DISTRIBUTE BY HASH ("suppkey")
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("DWS TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA = build_tpch_inserts(lambda table: f'"{table}"')

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
