#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Initialize TPC-H sample data in GaussDB for Datus integration testing.

Creates the five TPC-H tables (region, nation, customer, orders, supplier) and
populates them with the shared dataset used by the integration suite. The DDL
and rows come from ``datus_gaussdb.tpch_data`` — this script never holds a
second copy, so the tables it provisions are exactly the ones
``tests/integration/conftest.py::tpch_setup`` expects.

The DDL carries no distribution clause: openGauss centralized deployments
distribute nothing, and distributed deployments pick a default distribution key
when DISTRIBUTE BY is omitted.

Usage:
    python scripts/init_tpch_data.py \\
        --host 127.0.0.1 --port 25434 \\
        --username datus --password 'Datus@123' \\
        --database postgres --schema public

    # Drop existing tables first, then recreate
    python scripts/init_tpch_data.py --drop

Environment variables (used as defaults):
    GAUSSDB_HOST, GAUSSDB_PORT, GAUSSDB_USER, GAUSSDB_PASSWORD,
    GAUSSDB_DATABASE, GAUSSDB_SCHEMA
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in GaussDB")
    parser.add_argument("--host", default=os.getenv("GAUSSDB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("GAUSSDB_PORT", "25434")))
    parser.add_argument("--username", default=os.getenv("GAUSSDB_USER", "datus"))
    parser.add_argument("--password", default=os.getenv("GAUSSDB_PASSWORD", "Datus@123"))
    parser.add_argument("--database", default=os.getenv("GAUSSDB_DATABASE", "postgres"))
    parser.add_argument("--schema", default=os.getenv("GAUSSDB_SCHEMA", "public"))
    parser.add_argument("--drop", action="store_true", help="Drop existing tables before creating")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print("ERROR: --schema must be a valid SQL identifier (letters, digits, underscores).")
        sys.exit(1)

    try:
        from datus_gaussdb import GaussDBConfig, GaussDBConnector
        from datus_gaussdb.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES
    except ImportError:
        print("ERROR: datus-gaussdb is not installed.")
        print("  pip install -e ../datus-gaussdb")
        sys.exit(1)

    config = GaussDBConfig(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        schema_name=args.schema,
    )

    conn = None
    schema = args.schema

    try:
        conn = GaussDBConnector(config)
        if not conn.test_connection():
            print("ERROR: Connection test failed.")
            sys.exit(1)
        print(f"Connected to GaussDB at {args.host}:{args.port}/{args.database}")

        # Statements are unqualified; the connector applies `SET search_path`
        # from schema_name to every statement.
        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in reversed(TPCH_TABLES):
                conn.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE')
                print(f"  Dropped {table}")

        # Note: CREATE TABLE has no IF NOT EXISTS guard so that a stale schema
        # fails loudly; use --drop to recreate tables cleanly.
        print("\nCreating TPC-H tables...")
        for i, ddl in enumerate(TPCH_DDL):
            result = conn.execute_ddl(ddl)
            if not result.success:
                print(f"  Error creating {TPCH_TABLES[i]}: {result.error}")
                sys.exit(1)
            print(f"  Created {TPCH_TABLES[i]}")

        print("\nInserting TPC-H data...")
        for i, insert_sql in enumerate(TPCH_DATA):
            result = conn.execute_insert(insert_sql)
            if not result.success:
                print(f"  Error inserting into {TPCH_TABLES[i]}: {result.error}")
                sys.exit(1)
            print(f"  Inserted {ROW_COUNTS[i]} rows into {TPCH_TABLES[i]}")

        print("\nVerification:")
        has_mismatch = False
        for i, table in enumerate(TPCH_TABLES):
            result = conn.execute(
                {"sql_query": f'SELECT COUNT(*) AS cnt FROM "{schema}"."{table}"'},
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

        print("\nTPC-H data initialization complete!")

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
