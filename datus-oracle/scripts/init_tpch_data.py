#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Initialize TPC-H sample data in Oracle for Datus integration testing.

Creates the five TPC-H tables (region, nation, customer, orders, supplier) and
populates them with the shared dataset used by the integration suite. The DDL
and rows come from ``datus_oracle.tpch_data`` — this script never holds a
second copy, so the tables it provisions are exactly the ones
``tests/integration/conftest.py::tpch_setup`` expects.

Usage:
    # Start Oracle first:
    ORACLE_SYS_PASSWORD=... ORACLE_PASSWORD=... \\
        docker compose -f datus-oracle/docker-compose.yml up -d --wait

    # Then run this script:
    python scripts/init_tpch_data.py \\
        --host localhost --port 1521 \\
        --username datus_test --password test_password \\
        --service-name FREEPDB1 --schema DATUS_TEST

    # Drop existing tables first, then recreate
    python scripts/init_tpch_data.py --drop

Environment variables (used as defaults):
    ORACLE_HOST, ORACLE_PORT, ORACLE_USER, ORACLE_PASSWORD,
    ORACLE_SERVICE_NAME, ORACLE_SCHEMA
"""

import argparse
import os
import re
import sys


def drop_table_sql(table_ref: str) -> str:
    """DROP that tolerates a missing table on Oracle 19c; swallow ORA-00942 only."""
    return (
        "BEGIN "
        f"EXECUTE IMMEDIATE 'DROP TABLE {table_ref} CASCADE CONSTRAINTS'; "
        "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; "
        "END;"
    )


def main():
    parser = argparse.ArgumentParser(description="Initialize TPC-H data in Oracle")
    parser.add_argument("--host", default=os.getenv("ORACLE_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ORACLE_PORT", "1521")))
    parser.add_argument("--username", default=os.getenv("ORACLE_USER", "datus_test"))
    parser.add_argument("--password", default=os.getenv("ORACLE_PASSWORD", "test_password"))
    parser.add_argument(
        "--service-name",
        dest="service_name",
        default=os.getenv("ORACLE_SERVICE_NAME", "FREEPDB1"),
        help="Oracle service name / PDB (default: FREEPDB1)",
    )
    parser.add_argument(
        "--schema",
        default=os.getenv("ORACLE_SCHEMA", "DATUS_TEST"),
        help="Target schema (default: DATUS_TEST)",
    )
    parser.add_argument("--drop", action="store_true", help="Drop existing tables before creating")
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", args.schema):
        print("ERROR: --schema must be a valid SQL identifier (letters, digits, underscores).")
        sys.exit(1)

    try:
        from datus_oracle import OracleConfig, OracleConnector
        from datus_oracle.tpch_data import ROW_COUNTS, TPCH_DATA_BY_TABLE, TPCH_DDL, TPCH_TABLES
    except ImportError:
        print("ERROR: datus-oracle is not installed.")
        print("  pip install -e ../datus-oracle")
        sys.exit(1)

    config = OracleConfig(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        service_name=args.service_name,
        schema_name=args.schema,
    )

    conn = None
    try:
        conn = OracleConnector(config)
        if not conn.test_connection():
            print("ERROR: Connection test failed.")
            sys.exit(1)
        print(f"Connected to Oracle at {args.host}:{args.port}/{args.service_name}")

        schema_ref = conn.quote_identifier(args.schema)

        # Statements are unqualified; the connector applies
        # `ALTER SESSION SET CURRENT_SCHEMA` from schema_name to each one.
        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table in reversed(TPCH_TABLES):
                conn.execute_ddl(drop_table_sql(f'{schema_ref}."{table.upper()}"'))
                print(f"  Dropped {table}")

        print("\nCreating TPC-H tables...")
        for i, ddl in enumerate(TPCH_DDL):
            result = conn.execute_ddl(ddl)
            if not result.success:
                print(f"  Error creating {TPCH_TABLES[i]}: {result.error}")
                print("  Re-run with --drop if the tables already exist.")
                sys.exit(1)
            print(f"  Created {TPCH_TABLES[i]}")

        # Insert row by row: Oracle has no multi-row VALUES clause.
        print("\nInserting TPC-H data...")
        for i, (table, inserts) in enumerate(zip(TPCH_TABLES, TPCH_DATA_BY_TABLE)):
            for insert_sql in inserts:
                result = conn.execute_insert(insert_sql)
                if not result.success:
                    print(f"  Error inserting into {table}: {result.error}")
                    sys.exit(1)
            print(f"  Inserted {ROW_COUNTS[i]} rows into {table}")

        print("\nVerification:")
        has_mismatch = False
        for i, table in enumerate(TPCH_TABLES):
            result = conn.execute(
                {"sql_query": f'SELECT COUNT(*) AS cnt FROM {schema_ref}."{table.upper()}"'},
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
