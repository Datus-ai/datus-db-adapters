#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""
Initialize TPC-H sample data in Amazon Redshift.

Usage:
    # Run with environment variables:
    REDSHIFT_HOST=your-cluster.region.redshift.amazonaws.com \
    REDSHIFT_USERNAME=admin REDSHIFT_PASSWORD=secret \
    uv run python scripts/init_tpch_data.py

    # With command-line arguments:
    uv run python scripts/init_tpch_data.py \
        --host your-cluster.region.redshift.amazonaws.com \
        --username admin --password secret

    # Drop existing tables first (clean re-init):
    uv run python scripts/init_tpch_data.py --drop

    # Use a custom schema:
    uv run python scripts/init_tpch_data.py --schema my_test_schema
"""

import argparse
import logging
import os
import sys

# Suppress adapter registry warnings in workspace dev environment
logging.getLogger("datus.tools.db_tools.registry").setLevel(logging.ERROR)

from datus_redshift import RedshiftConfig, RedshiftConnector  # noqa: E402
from datus_redshift.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H sample data in Amazon Redshift")
    parser.add_argument(
        "--host",
        default=os.getenv("REDSHIFT_HOST"),
        help="Redshift host (or set REDSHIFT_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("REDSHIFT_PORT", "5439")),
        help="Redshift port (default: 5439)",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("REDSHIFT_USERNAME"),
        help="Username (or set REDSHIFT_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("REDSHIFT_PASSWORD"),
        help="Password (or set REDSHIFT_PASSWORD)",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("REDSHIFT_DATABASE", "dev"),
        help="Database (default: dev)",
    )
    parser.add_argument(
        "--schema",
        default=os.getenv("REDSHIFT_SCHEMA", "public"),
        help="Schema (default: public)",
    )
    parser.add_argument("--drop", action="store_true", help="Drop existing TPC-H tables before creating")
    args = parser.parse_args()

    if not args.host:
        print("Error: Redshift host is required. Use --host or set REDSHIFT_HOST.")
        sys.exit(1)
    if not args.username:
        print("Error: Username is required. Use --username or set REDSHIFT_USERNAME.")
        sys.exit(1)
    if not args.password:
        print("Error: Password is required. Use --password or set REDSHIFT_PASSWORD.")
        sys.exit(1)

    config = RedshiftConfig(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        schema=args.schema,
        ssl=True,
        timeout_seconds=30,
    )

    schema = args.schema

    print(f"Connecting to Redshift at {args.host}:{args.port}/{args.database} (schema: {schema})...")
    conn = RedshiftConnector(config)
    try:
        if not conn.test_connection():
            print("Failed to connect to Redshift. Check your credentials and cluster status.")
            sys.exit(1)

        print("Connected successfully!")

        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in TPCH_TABLES:
                result = conn.execute_ddl(f"DROP TABLE IF EXISTS {schema}.{table}")
                if not result.success:
                    print(f"  Warning: Failed to drop {table}: {result.error}")
                else:
                    print(f"  Dropped {table}")

        print("\nCreating TPC-H tables...")
        for i, ddl in enumerate(TPCH_DDL):
            result = conn.execute_ddl(ddl.format(schema=schema))
            if not result.success:
                print(f"  Error creating {TPCH_TABLES[i]}: {result.error}")
                sys.exit(1)
            print(f"  Created {TPCH_TABLES[i]}")

        print("\nInserting TPC-H data...")
        for i, data in enumerate(TPCH_DATA):
            result = conn.execute_insert(data.format(schema=schema))
            if not result.success:
                print(f"  Error inserting into {TPCH_TABLES[i]}: {result.error}")
                sys.exit(1)
            print(f"  Inserted {ROW_COUNTS[i]} rows into {TPCH_TABLES[i]}")

        # Verify
        print("\nVerifying data...")
        has_mismatch = False
        for i, table in enumerate(TPCH_TABLES):
            result = conn.execute(
                {"sql_query": f"SELECT COUNT(*) AS cnt FROM {schema}.{table}"},
                result_format="list",
            )
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
    print(f"  SELECT * FROM {schema}.tpch_region")
    print(
        f"  SELECT n.name, r.name FROM {schema}.tpch_nation n JOIN {schema}.tpch_region r ON n.regionkey = r.regionkey"
    )


if __name__ == "__main__":
    main()
