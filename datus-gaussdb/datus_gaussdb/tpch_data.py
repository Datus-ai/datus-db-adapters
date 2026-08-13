# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""PostgreSQL/GaussDB-dialect TPC-H test data definitions."""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

TPCH_DDL = [
    """
    CREATE TABLE "tpch_region" (
        "regionkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("regionkey")
    )
    """,
    """
    CREATE TABLE "tpch_nation" (
        "nationkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "regionkey" INTEGER NOT NULL,
        "comment" VARCHAR(152),
        PRIMARY KEY ("nationkey")
    )
    """,
    """
    CREATE TABLE "tpch_customer" (
        "custkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        "mktsegment" VARCHAR(10) NOT NULL,
        PRIMARY KEY ("custkey")
    )
    """,
    """
    CREATE TABLE "tpch_orders" (
        "orderkey" INTEGER NOT NULL,
        "custkey" INTEGER NOT NULL,
        "orderstatus" CHAR(1) NOT NULL,
        "totalprice" DECIMAL(15,2) NOT NULL,
        "orderdate" DATE NOT NULL,
        PRIMARY KEY ("orderkey")
    )
    """,
    """
    CREATE TABLE "tpch_supplier" (
        "suppkey" INTEGER NOT NULL,
        "name" VARCHAR(25) NOT NULL,
        "nationkey" INTEGER NOT NULL,
        "acctbal" DECIMAL(15,2) NOT NULL,
        PRIMARY KEY ("suppkey")
    )
    """,
]

TPCH_DATA = build_tpch_inserts(lambda t: f'"{t}"')

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
