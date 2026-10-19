"""HANA DDL; all row data comes from the shared TPC-H fixture."""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_row_inserts

_TPCH_DDL_ITEMS = [
    ("tpch_region", 'CREATE COLUMN TABLE "tpch_region" (regionkey INTEGER, name NVARCHAR(25), comment NVARCHAR(152))'),
    (
        "tpch_nation",
        'CREATE COLUMN TABLE "tpch_nation" (nationkey INTEGER, name NVARCHAR(25), regionkey INTEGER, comment NVARCHAR(152))',
    ),
    (
        "tpch_customer",
        'CREATE COLUMN TABLE "tpch_customer" (custkey INTEGER, name NVARCHAR(25), nationkey INTEGER, acctbal DECIMAL(15,2), mktsegment NVARCHAR(10))',
    ),
    (
        "tpch_orders",
        'CREATE COLUMN TABLE "tpch_orders" (orderkey INTEGER, custkey INTEGER, orderstatus NCHAR(1), totalprice DECIMAL(15,2), orderdate DATE)',
    ),
    (
        "tpch_supplier",
        'CREATE COLUMN TABLE "tpch_supplier" (suppkey INTEGER, name NVARCHAR(25), nationkey INTEGER, acctbal DECIMAL(15,2))',
    ),
]
if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("HANA TPC-H DDL order must match shared TPCH_TABLES")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA_BY_TABLE = build_tpch_row_inserts(lambda table: f'"{table}"', date_literal=True)
TPCH_DATA = [sql for statements in TPCH_DATA_BY_TABLE for sql in statements]
__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_DATA_BY_TABLE", "TPCH_TABLES", "ROW_COUNTS"]
