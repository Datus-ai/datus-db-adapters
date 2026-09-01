# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Oracle-dialect TPC-H test data definitions.

Two Oracle specifics drive the shape of this module:

- Oracle has no multi-row ``VALUES`` clause (``INSERT ALL`` is its stand-in),
  so ``TPCH_DATA`` is a flat list of single-row INSERT statements rather than
  one statement per table. ``TPCH_DATA_BY_TABLE`` keeps the per-table grouping
  for callers that report progress.
- DATE columns take explicit ``DATE 'YYYY-MM-DD'`` literals so loading does not
  depend on the session's NLS_DATE_FORMAT.

Identifiers are quoted upper-case, matching ``quote_oracle_identifier``. Table
names are unqualified: callers set the target schema through the connector's
``schema_name``, which is applied as ``ALTER SESSION SET CURRENT_SCHEMA``.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_row_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE "TPCH_REGION" (
        "REGIONKEY" NUMBER(10) NOT NULL,
        "NAME" VARCHAR2(25) NOT NULL,
        "COMMENT" VARCHAR2(152),
        PRIMARY KEY ("REGIONKEY")
    )
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE "TPCH_NATION" (
        "NATIONKEY" NUMBER(10) NOT NULL,
        "NAME" VARCHAR2(25) NOT NULL,
        "REGIONKEY" NUMBER(10) NOT NULL,
        "COMMENT" VARCHAR2(152),
        PRIMARY KEY ("NATIONKEY")
    )
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE "TPCH_CUSTOMER" (
        "CUSTKEY" NUMBER(10) NOT NULL,
        "NAME" VARCHAR2(25) NOT NULL,
        "NATIONKEY" NUMBER(10) NOT NULL,
        "ACCTBAL" NUMBER(15,2) NOT NULL,
        "MKTSEGMENT" VARCHAR2(10) NOT NULL,
        PRIMARY KEY ("CUSTKEY")
    )
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE "TPCH_ORDERS" (
        "ORDERKEY" NUMBER(10) NOT NULL,
        "CUSTKEY" NUMBER(10) NOT NULL,
        "ORDERSTATUS" CHAR(1) NOT NULL,
        "TOTALPRICE" NUMBER(15,2) NOT NULL,
        "ORDERDATE" DATE NOT NULL,
        PRIMARY KEY ("ORDERKEY")
    )
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE "TPCH_SUPPLIER" (
        "SUPPKEY" NUMBER(10) NOT NULL,
        "NAME" VARCHAR2(25) NOT NULL,
        "NATIONKEY" NUMBER(10) NOT NULL,
        "ACCTBAL" NUMBER(15,2) NOT NULL,
        PRIMARY KEY ("SUPPKEY")
    )
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("Oracle TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]

#: Single-row INSERTs grouped per table, parallel to TPCH_TABLES.
TPCH_DATA_BY_TABLE = build_tpch_row_inserts(lambda t: f'"{t.upper()}"', date_literal=True)

#: Flat list of every single-row INSERT, in TPCH_TABLES order.
TPCH_DATA = [stmt for table_stmts in TPCH_DATA_BY_TABLE for stmt in table_stmts]

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_DATA_BY_TABLE", "TPCH_TABLES", "ROW_COUNTS"]
