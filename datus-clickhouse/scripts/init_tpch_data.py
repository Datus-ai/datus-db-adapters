#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""
Initialize TPC-H sample data in ClickHouse.

Usage:
    # Start ClickHouse first:
    cd datus-clickhouse && docker compose up -d

    # Then run this script:
    uv run python scripts/init_tpch_data.py

    # With custom connection:
    uv run python scripts/init_tpch_data.py --host localhost --port 8123 --username default_user --password default_test

    # Drop existing tables first (clean re-init):
    uv run python scripts/init_tpch_data.py --drop
"""

import argparse
import logging
import sys

# Suppress adapter registry warnings in workspace dev environment
logging.getLogger("datus.tools.db_tools.registry").setLevel(logging.ERROR)

from datus_clickhouse import ClickHouseConnector  # noqa: E402
from datus_clickhouse.testing import config_from_env  # noqa: E402
from datus_clickhouse.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES  # noqa: E402


def _require_success(result, operation):
    if not result.success:
        raise RuntimeError(f"{operation} failed: {result.error}")


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H sample data in ClickHouse")
    parser.add_argument("--host", default=None, help="ClickHouse host (or CLICKHOUSE_HOST)")
    parser.add_argument("--port", type=int, default=None, help="HTTP(S) port (defaults by protocol)")
    parser.add_argument("--username", default=None, help="Username (or CLICKHOUSE_USER)")
    parser.add_argument("--password", default=None, help="Password (or CLICKHOUSE_PASSWORD)")
    parser.add_argument("--database", default=None, help="Database (or CLICKHOUSE_DATABASE)")
    parser.add_argument("--drop", action="store_true", help="Drop existing TPC-H tables before creating")
    parser.add_argument("--protocol", choices=("http", "https"), default=None)
    parser.add_argument("--ca-cert", default=None, help="PEM CA bundle file for HTTPS")
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    config = config_from_env({key: value for key, value in vars(args).items() if key != "drop" and value is not None})

    # First ensure database exists
    print(f"Connecting to ClickHouse at {config.host}:{config.port} ({config.protocol})...")
    init_config = config.model_copy(update={"database": None})
    init_conn = ClickHouseConnector(init_config)

    try:
        if not init_conn.test_connection():
            raise RuntimeError("ClickHouse connection test failed")
        print("Connected successfully!")
        print(f"\nEnsuring database `{config.database}` exists...")
        _require_success(init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{config.database}`"), "create database")
    finally:
        init_conn.close()

    # Connect to target database
    conn = ClickHouseConnector(config)
    try:
        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in TPCH_TABLES:
                _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`"), "drop table")
                print(f"  Dropped {table}")

        print("\nCreating TPC-H tables...")
        for i, ddl in enumerate(TPCH_DDL):
            _require_success(conn.execute_ddl(ddl), "create TPC-H table")
            print(f"  Created {TPCH_TABLES[i]}")

        print("\nInserting TPC-H data...")
        for i, data in enumerate(TPCH_DATA):
            _require_success(conn.execute_insert(data), "insert TPC-H rows")
            print(f"  Inserted {ROW_COUNTS[i]} rows into {TPCH_TABLES[i]}")

        # Verify
        print("\nVerifying data...")
        has_mismatch = False
        for i, table in enumerate(TPCH_TABLES):
            result = conn.execute(
                {"sql_query": f"SELECT COUNT(*) AS cnt FROM `{table}`"},
                result_format="list",
            )
            _require_success(result, "verify TPC-H count")
            count = result.sql_return[0]["cnt"]
            expected = ROW_COUNTS[i]
            status = "OK" if count == expected else "MISMATCH"
            if count != expected:
                has_mismatch = True
            print(f"  {table}: {count} rows [{status}]")

        if has_mismatch:
            print("\nVerification failed. Re-run with --drop for a clean re-init.")
            sys.exit(2)
    finally:
        conn.close()
    print("\nDone! TPC-H data is ready for use in Datus.")
    print("\nExample queries:")
    print("  SELECT * FROM `tpch_region`")
    print("  SELECT n.name, r.name FROM `tpch_nation` n JOIN `tpch_region` r ON n.regionkey = r.regionkey")


if __name__ == "__main__":
    main()
