#!/usr/bin/env python3
# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Wait until Doris can run test DDL safely."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import pymysql

DORIS_PRIVILEGE_ERROR_CODE = 5203


@dataclass(frozen=True)
class DorisReadinessConfig:
    host: str
    port: int
    username: str
    password: str
    catalog: str
    database: str


def quote_identifier(identifier: str) -> str:
    """Quote a Doris identifier."""
    return "`" + identifier.replace("`", "``") + "`"


def table_identifier(catalog: str, database: str, table: str) -> str:
    """Build a qualified, quoted Doris table identifier."""
    parts = [part for part in (catalog, database, table) if part]
    return ".".join(quote_identifier(part) for part in parts)


def is_alive(value: object) -> bool:
    """Interpret Doris SHOW BACKENDS Alive values."""
    return str(value).strip().lower() in {"true", "1", "yes"}


def positive_int(value: str) -> int:
    """Parse a strictly positive integer argument."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def positive_float(value: str) -> float:
    """Parse a strictly positive floating-point argument."""
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def check_doris_ready(config: DorisReadinessConfig) -> str:
    """Verify connectivity, backend health, and OLAP DDL readiness."""
    backend_detail = "backend status not checked"
    probe_table = f"__datus_doris_readiness_probe_{os.getpid()}_{int(time.time() * 1000)}"
    conn = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.username,
        password=config.password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            if not row or row[0] != 1:
                raise RuntimeError(f"unexpected SELECT 1 result: {row!r}")

            try:
                cursor.execute("SHOW BACKENDS")
            except pymysql.err.OperationalError as exc:
                # Doris reports missing SYSTEM OPERATE/NODE privileges as 5203.
                if not exc.args or exc.args[0] != DORIS_PRIVILEGE_ERROR_CODE:
                    raise
                backend_detail = "backend status unavailable without SYSTEM OPERATE/NODE privilege"
            else:
                rows = cursor.fetchall()
                columns = [description[0] for description in cursor.description or []]
                alive_index = next((index for index, column in enumerate(columns) if column.lower() == "alive"), None)
                if alive_index is None:
                    raise RuntimeError(f"SHOW BACKENDS did not return an Alive column: {columns!r}")
                alive_rows = [row for row in rows if is_alive(row[alive_index])]
                if not alive_rows:
                    raise RuntimeError(f"SHOW BACKENDS has no alive backend: columns={columns!r} rows={rows!r}")
                backend_detail = f"{len(alive_rows)} alive backend(s)"

            if config.database:
                if config.catalog:
                    cursor.execute(f"SWITCH {quote_identifier(config.catalog)}")
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {quote_identifier(config.database)}")
                full_probe_table = table_identifier(config.catalog, config.database, probe_table)
                create_error: BaseException | None = None
                try:
                    cursor.execute(
                        f"""
                        CREATE TABLE {full_probe_table} (
                            `id` INT
                        )
                        ENGINE=OLAP
                        DUPLICATE KEY(`id`)
                        DISTRIBUTED BY HASH(`id`) BUCKETS 1
                        PROPERTIES ("replication_num" = "1")
                        """
                    )
                    return f"{backend_detail}; database {config.database!r} accepts OLAP DDL"
                except BaseException as exc:
                    create_error = exc
                    raise
                finally:
                    try:
                        cursor.execute(f"DROP TABLE IF EXISTS {full_probe_table}")
                    except Exception as cleanup_error:
                        if create_error is None:
                            raise
                        create_error.add_note(f"Probe-table cleanup also failed: {cleanup_error}")
            return f"{backend_detail}; no database DDL probe requested"
    finally:
        conn.close()


def wait_for_doris_ready(config: DorisReadinessConfig, timeout: int, interval: float) -> str:
    """Poll until Doris passes the readiness probe or timeout expires."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            return check_doris_ready(config)
        except Exception as exc:  # noqa: BLE001 - readiness probes report the last failure.
            last_error = exc
            time.sleep(interval)

    if last_error is None:
        raise TimeoutError(f"timed out after {timeout}s waiting for Doris readiness")
    message = f"timed out after {timeout}s waiting for Doris readiness; last error: {last_error}"
    raise TimeoutError(message) from last_error


def build_parser() -> argparse.ArgumentParser:
    """Build the readiness-check command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("DORIS_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DORIS_PORT", "9030")))
    parser.add_argument("--username", default=os.getenv("DORIS_USER", "root"))
    parser.add_argument("--password", default=os.getenv("DORIS_PASSWORD", ""))
    parser.add_argument("--catalog", default=os.getenv("DORIS_CATALOG", "internal"))
    parser.add_argument("--database", default=os.getenv("DORIS_DATABASE", "test"))
    parser.add_argument("--timeout", type=positive_int, default=positive_int(os.getenv("DORIS_READY_TIMEOUT", "300")))
    parser.add_argument(
        "--interval",
        type=positive_float,
        default=positive_float(os.getenv("DORIS_READY_INTERVAL", "5")),
    )
    return parser


def main() -> int:
    """Run the Doris readiness-check command."""
    args = build_parser().parse_args()
    config = DorisReadinessConfig(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        catalog=args.catalog,
        database=args.database,
    )

    print(f"Waiting for Doris at {config.host}:{config.port}/{config.catalog}/{config.database}...", flush=True)
    try:
        detail = wait_for_doris_ready(config, timeout=args.timeout, interval=args.interval)
    except TimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Doris readiness check passed: {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
