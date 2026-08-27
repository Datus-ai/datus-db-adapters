#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Initialize TPC-H test data in TiDB.

Usage:
    python scripts/init_tpch_data.py [--tiflash]

Environment:
    TIDB_HOST, TIDB_PORT, TIDB_USER, TIDB_PASSWORD, TIDB_DATABASE
"""

import argparse
import os
import sys

from datus_tidb import TiDBConfig, TiDBConnector
from datus_tidb.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES


def _config() -> TiDBConfig:
    return TiDBConfig(
        host=os.getenv("TIDB_HOST", "127.0.0.1"),
        port=int(os.getenv("TIDB_PORT", "4000")),
        username=os.getenv("TIDB_USER", "root"),
        password=os.getenv("TIDB_PASSWORD", ""),
        database=os.getenv("TIDB_DATABASE", "test"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in TiDB")
    parser.add_argument(
        "--tiflash",
        action="store_true",
        help="Also grant each table a TiFlash columnar replica",
    )
    args = parser.parse_args()

    config = _config()
    connector = TiDBConnector(config)
    try:
        if not connector.test_connection():
            print(f"Cannot reach TiDB at {config.host}:{config.port}", file=sys.stderr)
            return 1

        for table in TPCH_TABLES:
            connector.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
        for ddl in TPCH_DDL:
            connector.execute_ddl(ddl)
        for data in TPCH_DATA:
            connector.execute_insert(data)

        for table, expected in zip(TPCH_TABLES, ROW_COUNTS):
            result = connector.execute({"sql_query": f"SELECT COUNT(*) AS c FROM `{table}`"}, result_format="list")
            actual = int(result.sql_return[0]["c"]) if result.success else -1
            status = "ok" if actual == expected else f"expected {expected}"
            print(f"  {table}: {actual} rows ({status})")

        if args.tiflash:
            for table in TPCH_TABLES:
                connector.execute_ddl(f"ALTER TABLE `{table}` SET TIFLASH REPLICA 1")
            print("TiFlash replicas requested; they sync asynchronously.")
            print("Check: SELECT * FROM information_schema.TIFLASH_REPLICA")

        print(f"TPC-H data ready in `{config.database}`")
        return 0
    finally:
        connector.close()


if __name__ == "__main__":
    sys.exit(main())
