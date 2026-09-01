#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Initialize TPC-H sample data in Hologres for Datus integration testing.

Creates the five TPC-H tables (region, nation, customer, orders, supplier) and
populates them with the shared dataset used by the integration suite. The DDL
and rows come from ``datus_hologres.tpch_data`` — this script never holds a
second copy, so the tables it provisions are exactly the ones
``tests/integration/conftest.py::tpch_setup`` expects.

Hologres authenticates with an Alibaba Cloud AccessKey pair, which the config
accepts under either the ``username``/``password`` or the
``access_key_id``/``access_key_secret`` names.

Usage:
    python scripts/init_tpch_data.py \\
        --host my-instance.hologres.aliyuncs.com --port 80 \\
        --access-key-id "$ALIBABA_CLOUD_ACCESS_KEY_ID" \\
        --access-key-secret "$ALIBABA_CLOUD_ACCESS_KEY_SECRET" \\
        --database mydb --schema public

    # Drop existing tables first, then recreate
    python scripts/init_tpch_data.py --drop

Environment variables (used as defaults):
    HOLOGRES_HOST, HOLOGRES_PORT, HOLOGRES_ACCESS_KEY_ID,
    HOLOGRES_ACCESS_KEY_SECRET, HOLOGRES_DATABASE, HOLOGRES_SCHEMA,
    HOLOGRES_SSLMODE
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in Hologres")
    parser.add_argument("--host", default=os.getenv("HOLOGRES_HOST"), help="Hologres endpoint (or set HOLOGRES_HOST)")
    parser.add_argument("--port", type=int, default=int(os.getenv("HOLOGRES_PORT") or "80"))
    parser.add_argument(
        "--access-key-id",
        dest="access_key_id",
        default=os.getenv("HOLOGRES_ACCESS_KEY_ID"),
        help="Alibaba Cloud AccessKey ID (or set HOLOGRES_ACCESS_KEY_ID)",
    )
    parser.add_argument(
        "--access-key-secret",
        dest="access_key_secret",
        default=os.getenv("HOLOGRES_ACCESS_KEY_SECRET"),
        help="Alibaba Cloud AccessKey Secret (or set HOLOGRES_ACCESS_KEY_SECRET)",
    )
    parser.add_argument(
        "--database", default=os.getenv("HOLOGRES_DATABASE"), help="Database (or set HOLOGRES_DATABASE)"
    )
    parser.add_argument("--schema", default=os.getenv("HOLOGRES_SCHEMA") or "public")
    parser.add_argument("--sslmode", default=os.getenv("HOLOGRES_SSLMODE") or "prefer")
    parser.add_argument("--drop", action="store_true", help="Drop existing tables before creating")
    args = parser.parse_args()

    missing = [
        name
        for name, value in (
            ("--host", args.host),
            ("--access-key-id", args.access_key_id),
            ("--access-key-secret", args.access_key_secret),
            ("--database", args.database),
        )
        if not value
    ]
    if missing:
        print(f"ERROR: missing required argument(s): {', '.join(missing)}")
        sys.exit(1)

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print("ERROR: --schema must be a valid SQL identifier (letters, digits, underscores).")
        sys.exit(1)

    try:
        from datus_hologres import HologresConfig, HologresConnector
        from datus_hologres.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES
    except ImportError:
        print("ERROR: datus-hologres is not installed.")
        print("  pip install -e ../datus-hologres")
        sys.exit(1)

    config = HologresConfig(
        host=args.host,
        port=args.port,
        access_key_id=args.access_key_id,
        access_key_secret=args.access_key_secret,
        database=args.database,
        schema=args.schema,
        sslmode=args.sslmode,
    )

    conn = None
    schema = args.schema

    try:
        conn = HologresConnector(config)
        if not conn.test_connection():
            print("ERROR: Connection test failed.")
            sys.exit(1)
        print(f"Connected to Hologres at {config.host}:{config.port}/{args.database}")

        # Statements are unqualified; the connector applies `SET search_path`
        # from schema_name to every statement.
        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in reversed(TPCH_TABLES):
                conn.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE')
                print(f"  Dropped {table}")

        print("\nCreating TPC-H tables...")
        for i, ddl in enumerate(TPCH_DDL):
            result = conn.execute_ddl(ddl)
            if not result.success:
                print(f"  Error creating {TPCH_TABLES[i]}: {result.error}")
                print("  Re-run with --drop if the tables already exist.")
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
