#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Initialize TPC-H sample data in GaussDB for Datus integration testing.

This script creates five TPC-H tables (region, nation, supplier, customer,
orders) and populates them with a small representative dataset.

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

# ---------------------------------------------------------------------------
# TPC-H DDL (PostgreSQL-compatible syntax, no distribution clause: openGauss
# centralized deployments distribute nothing, and distributed deployments pick
# a default distribution key when DISTRIBUTE BY is omitted)
# ---------------------------------------------------------------------------

TPCH_DDL = {
    "tpch_region": """
        CREATE TABLE "{schema}"."tpch_region" (
            "r_regionkey" INTEGER NOT NULL,
            "r_name"      VARCHAR(25) NOT NULL,
            "r_comment"   VARCHAR(152),
            PRIMARY KEY ("r_regionkey")
        )
    """,
    "tpch_nation": """
        CREATE TABLE "{schema}"."tpch_nation" (
            "n_nationkey"  INTEGER NOT NULL,
            "n_name"       VARCHAR(25) NOT NULL,
            "n_regionkey"  INTEGER NOT NULL,
            "n_comment"    VARCHAR(152),
            PRIMARY KEY ("n_nationkey")
        )
    """,
    "tpch_supplier": """
        CREATE TABLE "{schema}"."tpch_supplier" (
            "s_suppkey"  INTEGER NOT NULL,
            "s_name"     VARCHAR(25) NOT NULL,
            "s_address"  VARCHAR(40) NOT NULL,
            "s_nationkey" INTEGER NOT NULL,
            "s_phone"    VARCHAR(15) NOT NULL,
            "s_acctbal"  DECIMAL(15,2) NOT NULL,
            "s_comment"  VARCHAR(101),
            PRIMARY KEY ("s_suppkey")
        )
    """,
    "tpch_customer": """
        CREATE TABLE "{schema}"."tpch_customer" (
            "c_custkey"    INTEGER NOT NULL,
            "c_name"       VARCHAR(25) NOT NULL,
            "c_address"    VARCHAR(40) NOT NULL,
            "c_nationkey"  INTEGER NOT NULL,
            "c_phone"      VARCHAR(15) NOT NULL,
            "c_acctbal"    DECIMAL(15,2) NOT NULL,
            "c_mktsegment" VARCHAR(10) NOT NULL,
            "c_comment"    VARCHAR(117),
            PRIMARY KEY ("c_custkey")
        )
    """,
    "tpch_orders": """
        CREATE TABLE "{schema}"."tpch_orders" (
            "o_orderkey"      INTEGER NOT NULL,
            "o_custkey"       INTEGER NOT NULL,
            "o_orderstatus"   CHAR(1) NOT NULL,
            "o_totalprice"    DECIMAL(15,2) NOT NULL,
            "o_orderdate"     DATE NOT NULL,
            "o_orderpriority" VARCHAR(15) NOT NULL,
            "o_clerk"         VARCHAR(15) NOT NULL,
            "o_shippriority"  INTEGER NOT NULL,
            "o_comment"       VARCHAR(79),
            PRIMARY KEY ("o_orderkey")
        )
    """,
}

# ---------------------------------------------------------------------------
# TPC-H Sample Data
# ---------------------------------------------------------------------------

TPCH_DATA = {
    "tpch_region": [
        (0, "AFRICA", "lar deposits. blithely final packages cajole."),
        (1, "AMERICA", "hs use ironic, even requests."),
        (2, "ASIA", "ges. thinly even pinto beans ca"),
        (3, "EUROPE", "ly final courts cajole furiously final excuse"),
        (4, "MIDDLE EAST", "uickly special accounts cajole carefully blithely close"),
    ],
    "tpch_nation": [
        (0, "ALGERIA", 0, " haggle. carefully final deposits detect slyly agai"),
        (1, "ARGENTINA", 1, "al foxes promise slyly according to the regular accounts."),
        (2, "BRAZIL", 1, "y alongside of the pending deposits."),
        (3, "CANADA", 1, "eas hang ironic, silent packages."),
        (4, "EGYPT", 4, "y above the carefully unusual theodolites."),
        (5, "ETHIOPIA", 0, "ven packages wake quickly."),
        (6, "FRANCE", 3, "refully final requests."),
        (7, "GERMANY", 3, "l platelets. regular accounts x-ray."),
        (8, "INDIA", 2, "ss excuses cajole slyly across the packages."),
        (9, "INDONESIA", 2, " slyly express asymptotes."),
        (10, "IRAN", 4, "efully alongside of the slyly final dependencies."),
        (11, "IRAQ", 4, "nic deposits boost atop the quickly final requests?"),
        (12, "JAPAN", 2, "ously. final, express gifts cajole a"),
        (13, "JORDAN", 4, "ic deposits are blithely about the carefully regular pa"),
        (14, "KENYA", 0, " pending excuses haggle furiously deposits."),
        (15, "MOROCCO", 0, "rns. blithely bold courts among the closely regular packages"),
        (16, "MOZAMBIQUE", 0, "s. ironic, unusual asymptotes wake blithely r"),
        (17, "PERU", 1, "platelets. blithely pending dependencies use fluffily"),
        (18, "CHINA", 2, "c dependencies. furiously express notornis sleep slyly"),
        (19, "ROMANIA", 3, "ular asymptotes are about the furious multipliers."),
        (20, "SAUDI ARABIA", 4, "ts. silent requests haggle. closely express packages"),
        (21, "VIETNAM", 2, "hely enticingly express accounts."),
        (22, "RUSSIA", 3, " requests against the platelets use never according to a"),
        (23, "UNITED KINGDOM", 3, "eans boost carefully special requests."),
        (24, "UNITED STATES", 1, "y final packages. slow foxes cajole quickly."),
    ],
    "tpch_supplier": [
        (
            1,
            "Supplier#000000001",
            "N kD4on9OM Ipw3,gf0JBoQDd7tgrzrddZ",
            17,
            "27-918-335-1736",
            5755.94,
            "each slyly above the careful",
        ),
        (2, "Supplier#000000002", "89eJ5ksX3ImxJQBvxObC,", 5, "15-679-861-2259", 4032.68, " slyly bold instructions."),
        (
            3,
            "Supplier#000000003",
            "q1,G3Pj6OjIuUYfUoH18BFTKP5aU9bEV3",
            1,
            "11-383-516-1199",
            4192.40,
            "blithely silent requests after the express dependencies",
        ),
        (
            4,
            "Supplier#000000004",
            "Bk7ah4CK8SYQTepEmvMkkgMwg",
            15,
            "25-843-787-7479",
            4641.08,
            "riously even requests above the exp",
        ),
        (5, "Supplier#000000005", "Gcdm2rJRzl5qlTVzc", 11, "21-151-690-3663", -531.44, ". slyly regular pinto beans t"),
    ],
    "tpch_customer": [
        (
            1,
            "Customer#000000001",
            "IVhzIApeRb",
            15,
            "25-989-741-2988",
            711.56,
            "BUILDING",
            "to the even, regular platelets.",
        ),
        (
            2,
            "Customer#000000002",
            "XSTf4,NCwDVaWNe6tEgvwfmRchLXak",
            13,
            "23-768-687-3665",
            121.65,
            "AUTOMOBILE",
            "l accounts. blithely ironic theodolites integrate boldly.",
        ),
        (
            3,
            "Customer#000000003",
            "MG9kdTD2WBHm",
            1,
            "11-719-748-3364",
            7498.12,
            "AUTOMOBILE",
            " deposits eat slyly ironic, even instructions.",
        ),
        (
            4,
            "Customer#000000004",
            "XxVSJsLAGtn",
            4,
            "14-128-190-5944",
            2866.83,
            "MACHINERY",
            " requests. final, regular ideas sleep final accou",
        ),
        (
            5,
            "Customer#000000005",
            "KvpyuHCplrB84WgAiGV6sYpZq7Tj",
            3,
            "13-750-942-6364",
            794.47,
            "HOUSEHOLD",
            "n accounts will have to unwind.",
        ),
        (
            6,
            "Customer#000000006",
            "sKZz0CsnMD7mp4Xd0YrBvx,LREYKUWAh",
            20,
            "30-114-968-4951",
            7638.57,
            "AUTOMOBILE",
            "tions. even deposits boost according to the slyly bold packages.",
        ),
        (
            7,
            "Customer#000000007",
            "TcGe5gaZNgVePxU5kRrvXBfkasDTea",
            18,
            "28-190-982-9759",
            9561.95,
            "AUTOMOBILE",
            "ainst the ironic, express theodolites.",
        ),
        (
            8,
            "Customer#000000008",
            "I0B10bB0AymmC,0PrRYBCP1yGJ8xcBPmWhl5",
            17,
            "27-147-574-9335",
            6819.74,
            "BUILDING",
            "among the slyly regular theodolites kindle blithely courts.",
        ),
        (
            9,
            "Customer#000000009",
            "xKiAFTjUsCuxfeleNqefumTrjS",
            8,
            "18-338-906-3675",
            8324.07,
            "FURNITURE",
            "r theodolites according to the requests wake thinly excuses:",
        ),
        (
            10,
            "Customer#000000010",
            "6LrEaV6KR6PLVcgl2ArL Q3rqzLzcT1 v2",
            5,
            "15-741-346-9870",
            2753.54,
            "HOUSEHOLD",
            "es regular deposits haggle. fur",
        ),
    ],
    "tpch_orders": [
        (1, 1, "O", 173665.47, "1996-01-02", "5-LOW", "Clerk#000000951", 0, "nstructions sleep furiously among"),
        (
            2,
            2,
            "O",
            46929.18,
            "1996-12-01",
            "1-URGENT",
            "Clerk#000000880",
            0,
            " foxes. pending accounts at the pending",
        ),
        (3, 3, "F", 193846.25, "1993-10-14", "5-LOW", "Clerk#000000955", 0, "sly final accounts boost."),
        (4, 4, "O", 32151.78, "1995-10-11", "5-LOW", "Clerk#000000124", 0, "sits. slyly regular warthogs cajole."),
        (5, 5, "F", 144659.20, "1994-07-30", "5-LOW", "Clerk#000000925", 0, "quickly. bold deposits sleep slyly."),
        (
            6,
            6,
            "F",
            58749.59,
            "1992-02-21",
            "4-NOT SPECIFIED",
            "Clerk#000000058",
            0,
            "ggle. special, final requests are against the furiously",
        ),
        (7, 7, "O", 252004.18, "1996-01-10", "2-HIGH", "Clerk#000000470", 0, "ly special requests"),
        (32, 8, "O", 208660.75, "1995-07-16", "2-HIGH", "Clerk#000000616", 0, "ise blithely bold, regular requests."),
        (33, 9, "F", 163243.98, "1993-10-27", "3-MEDIUM", "Clerk#000000409", 0, "uriously. furiously final request"),
        (
            34,
            10,
            "O",
            58949.67,
            "1998-07-21",
            "3-MEDIUM",
            "Clerk#000000223",
            0,
            "ly final packages. fluffily final deposits wake",
        ),
        (
            35,
            1,
            "O",
            56252.09,
            "1995-10-23",
            "4-NOT SPECIFIED",
            "Clerk#000000259",
            0,
            "zzle. carefully enticing deposits nag furio",
        ),
        (36, 2, "O", 38988.98, "1995-11-03", "1-URGENT", "Clerk#000000358", 0, " quick packages are blithely."),
        (37, 3, "F", 131896.48, "1992-06-03", "3-MEDIUM", "Clerk#000000456", 0, "kly regular pinto beans."),
        (38, 4, "O", 46366.56, "1996-08-21", "4-NOT SPECIFIED", "Clerk#000000604", 0, "haggle blithely."),
        (39, 5, "O", 219707.84, "1996-09-20", "3-MEDIUM", "Clerk#000000659", 0, "ole express, ironic requests:"),
    ],
}


def _escape_value(v) -> str:
    """Escape a value for SQL insertion."""
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


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

        # Drop tables if requested (always drop to ensure clean state)
        if args.drop:
            print("\nDropping existing TPC-H tables...")
            for table_name in reversed(list(TPCH_DDL.keys())):
                conn.execute({"sql_query": f'DROP TABLE IF EXISTS "{schema}"."{table_name}" CASCADE'})
                print(f"  Dropped {table_name}")

        # Create tables
        # Note: CREATE TABLE has no IF NOT EXISTS guard here so that a stale
        # schema fails loudly; use --drop to recreate tables cleanly.
        print("\nCreating TPC-H tables...")
        for table_name, ddl in TPCH_DDL.items():
            conn.execute({"sql_query": ddl.format(schema=schema)})
            print(f"  Created {table_name}")

        # Insert data
        print("\nInserting TPC-H data...")
        for table_name, rows in TPCH_DATA.items():
            for row in rows:
                values = ", ".join(_escape_value(v) for v in row)
                conn.execute({"sql_query": f'INSERT INTO "{schema}"."{table_name}" VALUES ({values})'})
            print(f"  Inserted {len(rows)} rows into {table_name}")

        # Verify
        print("\nVerification:")
        for table_name in TPCH_DDL:
            result = conn.execute(
                {"sql_query": f'SELECT COUNT(*) AS cnt FROM "{schema}"."{table_name}"'},
                result_format="list",
            )
            count = result.sql_return[0]["cnt"]
            print(f"  {table_name}: {count} rows")

        print("\nTPC-H data initialization complete!")

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
