#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Initialize the shared TPC-H sample dataset in Huawei Cloud DWS.

The DDL and rows come from ``datus_dws.tpch_data``, so this command provisions
the same five tables that ``tests/integration/conftest.py::tpch_setup`` uses.

Usage:
    export DWS_PASSWORD=...
    python scripts/init_tpch_data.py \
        --host example.dws.myhuaweicloud.com --port 8000 \
        --username dbadmin \
        --database gaussdb --schema main

    # Drop existing TPC-H tables first, then recreate them.
    python scripts/init_tpch_data.py --drop

Environment variables (used as defaults):
    DWS_HOST, DWS_PORT, DWS_USERNAME, DWS_PASSWORD, DWS_DATABASE, DWS_SCHEMA,
    DWS_SSLMODE, DWS_SSLROOTCERT
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in Huawei Cloud DWS")
    parser.add_argument("--host", default=os.getenv("DWS_HOST"), help="DWS endpoint (or set DWS_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ["DWS_PORT"]) if os.getenv("DWS_PORT") else None)
    parser.add_argument("--username", default=os.getenv("DWS_USERNAME"), help="DWS user (or set DWS_USERNAME)")
    parser.add_argument("--password", default=os.getenv("DWS_PASSWORD"), help="DWS password (or set DWS_PASSWORD)")
    parser.add_argument("--database", default=os.getenv("DWS_DATABASE") or "gaussdb")
    parser.add_argument("--schema", default=os.getenv("DWS_SCHEMA") or "public")
    parser.add_argument("--sslmode", default=os.getenv("DWS_SSLMODE") or "prefer")
    parser.add_argument("--sslrootcert", default=os.getenv("DWS_SSLROOTCERT"))
    parser.add_argument("--drop", action="store_true", help="Drop existing TPC-H tables before creating")
    args = parser.parse_args()

    missing = [
        name
        for name, value in (("--host", args.host), ("--username", args.username), ("--password", args.password))
        if not value
    ]
    if missing:
        print(f"ERROR: missing required argument(s): {', '.join(missing)}")
        sys.exit(1)

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print("ERROR: --schema must be a valid SQL identifier (letters, digits, underscores).")
        sys.exit(1)

    try:
        from datus_dws import DWSConfig, DWSConnector
        from datus_dws.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES
    except ImportError:
        print("ERROR: datus-dws is not installed.")
        print("  pip install -e ../datus-dws")
        sys.exit(1)

    config = DWSConfig(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        schema=args.schema,
        sslmode=args.sslmode,
        sslrootcert=args.sslrootcert,
    )

    connector = None
    try:
        connector = DWSConnector(config)
        if not connector.test_connection():
            print("ERROR: Connection test failed.")
            sys.exit(1)
        print(f"Connected to DWS at {config.host}:{config.port}/{args.database}")

        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in reversed(TPCH_TABLES):
                result = connector.execute_ddl(f'DROP TABLE IF EXISTS "{args.schema}"."{table}" CASCADE')
                if not result.success:
                    print(f"  Error dropping {table}: {result.error}")
                    sys.exit(1)
                print(f"  Dropped {table}")

        print("\nCreating TPC-H tables...")
        for table, ddl in zip(TPCH_TABLES, TPCH_DDL):
            result = connector.execute_ddl(ddl)
            if not result.success:
                print(f"  Error creating {table}: {result.error}")
                print("  Re-run with --drop if the tables already exist.")
                sys.exit(1)
            print(f"  Created {table}")

        print("\nInserting TPC-H data...")
        for table, expected, insert_sql in zip(TPCH_TABLES, ROW_COUNTS, TPCH_DATA):
            result = connector.execute_insert(insert_sql)
            if not result.success:
                print(f"  Error inserting into {table}: {result.error}")
                sys.exit(1)
            print(f"  Inserted {expected} rows into {table}")

        print("\nVerification:")
        has_mismatch = False
        for table, expected in zip(TPCH_TABLES, ROW_COUNTS):
            result = connector.execute(
                {"sql_query": f'SELECT COUNT(*) AS count FROM "{args.schema}"."{table}"'},
                result_format="list",
            )
            if not result.success:
                print(f"  Error querying {table}: {result.error}")
                sys.exit(1)
            count = result.sql_return[0]["count"]
            status = "OK" if count == expected else "MISMATCH"
            has_mismatch = has_mismatch or count != expected
            print(f"  {table}: {count} rows [{status}]")

        if has_mismatch:
            print("\nVerification failed. Re-run with --drop for a clean re-init.")
            sys.exit(2)

        print("\nTPC-H data initialization complete!")
    finally:
        if connector is not None:
            try:
                connector.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
