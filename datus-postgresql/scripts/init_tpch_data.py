#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Initialize TPC-H sample data in PostgreSQL for Datus integration testing.

Creates the five TPC-H tables (region, nation, customer, orders, supplier) and
populates them with the shared dataset used by the integration suite. The DDL
and rows come from ``datus_postgresql.tpch_data`` — this script never holds a
second copy.

Usage:
    python scripts/init_tpch_data.py \\
        --host localhost --port 5432 \\
        --username test_user --password test_password \\
        --database test --schema public

    # Drop existing tables first, then recreate
    python scripts/init_tpch_data.py --drop

Environment variables (used as defaults):
    POSTGRESQL_HOST, POSTGRESQL_PORT, POSTGRESQL_USER, POSTGRESQL_PASSWORD,
    POSTGRESQL_DATABASE, POSTGRESQL_SCHEMA
"""

import argparse
import os
import re
import sys


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in PostgreSQL")
    parser.add_argument("--host", default=os.getenv("POSTGRESQL_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("POSTGRESQL_PORT", "5432")))
    parser.add_argument("--username", default=os.getenv("POSTGRESQL_USER", "test_user"))
    parser.add_argument("--password", default=os.getenv("POSTGRESQL_PASSWORD", "test_password"))
    parser.add_argument("--database", default=os.getenv("POSTGRESQL_DATABASE", "test"))
    parser.add_argument("--schema", default=os.getenv("POSTGRESQL_SCHEMA", "public"))
    parser.add_argument("--drop", action="store_true", help="Drop existing tables before creating")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
        print("ERROR: --schema must be a valid SQL identifier (letters, digits, underscores).")
        sys.exit(1)

    try:
        from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector
        from datus_postgresql.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES
    except ImportError:
        print("ERROR: datus-postgresql is not installed.")
        print("  pip install -e ../datus-postgresql")
        sys.exit(1)

    config = PostgreSQLConfig(
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
        conn = PostgreSQLConnector(config)
        if not conn.test_connection():
            print("ERROR: Connection test failed.")
            sys.exit(1)
        print(f"Connected to PostgreSQL at {args.host}:{args.port}/{args.database}")

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
                sys.exit(1)
            print(f"  Created {TPCH_TABLES[i]}")

        # ON CONFLICT DO NOTHING keeps a re-run without --drop idempotent.
        print("\nInserting TPC-H data...")
        for i, insert_sql in enumerate(TPCH_DATA):
            result = conn.execute_insert(f"{insert_sql.rstrip()} ON CONFLICT DO NOTHING")
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
