# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""TiDB-dialect TPC-H test data definitions.

Plain MySQL DDL: TiDB has no distribution, bucketing, or table-model clause.
``ENGINE=InnoDB`` is accepted and inert — the storage engine is always TiKV.
"""

from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

_TPCH_DDL_ITEMS = [
    (
        "tpch_region",
        """
    CREATE TABLE IF NOT EXISTS `tpch_region` (
        `regionkey` INT NOT NULL,
        `name` VARCHAR(25) NOT NULL,
        `comment` VARCHAR(152),
        PRIMARY KEY (`regionkey`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "tpch_nation",
        """
    CREATE TABLE IF NOT EXISTS `tpch_nation` (
        `nationkey` INT NOT NULL,
        `name` VARCHAR(25) NOT NULL,
        `regionkey` INT NOT NULL,
        `comment` VARCHAR(152),
        PRIMARY KEY (`nationkey`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "tpch_customer",
        """
    CREATE TABLE IF NOT EXISTS `tpch_customer` (
        `custkey` INT NOT NULL,
        `name` VARCHAR(25) NOT NULL,
        `nationkey` INT NOT NULL,
        `acctbal` DECIMAL(15,2) NOT NULL,
        `mktsegment` VARCHAR(10) NOT NULL,
        PRIMARY KEY (`custkey`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "tpch_orders",
        """
    CREATE TABLE IF NOT EXISTS `tpch_orders` (
        `orderkey` INT NOT NULL,
        `custkey` INT NOT NULL,
        `orderstatus` VARCHAR(1) NOT NULL,
        `totalprice` DECIMAL(15,2) NOT NULL,
        `orderdate` DATE NOT NULL,
        PRIMARY KEY (`orderkey`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
    (
        "tpch_supplier",
        """
    CREATE TABLE IF NOT EXISTS `tpch_supplier` (
        `suppkey` INT NOT NULL,
        `name` VARCHAR(25) NOT NULL,
        `nationkey` INT NOT NULL,
        `acctbal` DECIMAL(15,2) NOT NULL,
        PRIMARY KEY (`suppkey`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    ),
]

if [table for table, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
    raise ValueError("TiDB TPC-H DDL order must match the shared TPCH_TABLES order")

TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
TPCH_DATA = build_tpch_inserts(lambda t: f"`{t}`")

__all__ = ["TPCH_DDL", "TPCH_DATA", "TPCH_TABLES", "ROW_COUNTS"]
