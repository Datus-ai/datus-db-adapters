#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Wait until TiDB is ready to serve, including its TiFlash store.

A reachable TiDB is not the whole cluster: TiFlash registers itself with PD a
moment after the SQL port opens, and `ALTER TABLE ... SET TIFLASH REPLICA` is
rejected until it does ("the tiflash replica count: 1 should be less than the
total tiflash server count: 0"). Callers that seed columnar tables must wait for
both.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import pymysql

TIFLASH_STORE_COUNT = "SELECT COUNT(*) FROM information_schema.TIKV_STORE_STATUS WHERE LABEL LIKE '%tiflash%'"


def positive_float(value: str) -> float:
    """Parse a strictly positive floating-point argument."""
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def check_once(host: str, port: int, username: str, password: str, require_tiflash: bool) -> None:
    """Raise unless TiDB answers and, when required, a TiFlash store is up."""
    connection = pymysql.connect(
        host=host,
        port=port,
        user=username,
        password=password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version = str(cursor.fetchone()[0])
            if "tidb" not in version.lower():
                raise RuntimeError(f"server does not identify as TiDB: {version}")

            if require_tiflash:
                cursor.execute(TIFLASH_STORE_COUNT)
                if int(cursor.fetchone()[0]) < 1:
                    raise RuntimeError("no TiFlash store has registered with PD yet")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for TiDB readiness")
    parser.add_argument("--host", default=os.getenv("TIDB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TIDB_PORT", "4000")))
    parser.add_argument("--username", default=os.getenv("TIDB_USER", "root"))
    parser.add_argument("--password", default=os.getenv("TIDB_PASSWORD", ""))
    parser.add_argument("--timeout", type=positive_float, default=300.0)
    parser.add_argument("--interval", type=positive_float, default=2.0)
    parser.add_argument(
        "--skip-tiflash",
        action="store_true",
        help="Only wait for the SQL port (a unistore cluster has no TiFlash)",
    )
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            check_once(args.host, args.port, args.username, args.password, not args.skip_tiflash)
        except Exception as error:  # noqa: BLE001 - report whatever kept the cluster busy
            last_error = error
            time.sleep(args.interval)
        else:
            print(f"TiDB is ready at {args.host}:{args.port}")
            return 0

    print(f"Timed out waiting for TiDB at {args.host}:{args.port}: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
