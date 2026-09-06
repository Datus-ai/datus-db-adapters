#!/usr/bin/env python3

"""Create the compatibility databases a fresh DWS cluster does not ship with.

`DBCOMPATIBILITY` is fixed at creation, so TD and MYSQL need databases of their
own; ORA reuses the cluster's default. Names match what dws-cloud-tests.yml
derives. Reads DWS_HOST / _PORT / _DATABASE / _USERNAME / _PASSWORD.
"""

from __future__ import annotations

import argparse
import os
import sys

# ORA is deliberately absent: the cluster's default database already provides it.
COMPATIBILITY_MODES = {
    "datus_ci_td": "TD",
    "datus_ci_mysql": "MYSQL",
}


class DatabaseError(RuntimeError):
    """A configuration or provisioning failure worth failing the job for."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise DatabaseError(f"{name} is required")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise DatabaseError(f"{name} must be an integer, got {raw!r}") from exc


def ssl_settings() -> dict[str, str]:
    """TLS for the admin password's trip to the cluster's public EIP.

    `require`, not libpq's `prefer`, which falls back to plaintext when the
    server offers no TLS — a fallback an attacker can provoke. Setting
    DWS_SSLROOTCERT upgrades this to `verify-ca`; see the README for why that
    is left optional here.
    """

    sslrootcert = os.getenv("DWS_SSLROOTCERT", "").strip()
    sslmode = os.getenv("DWS_SSLMODE", "").strip() or ("verify-ca" if sslrootcert else "require")
    if sslmode.startswith("verify") and not sslrootcert:
        raise DatabaseError(
            f"DWS_SSLMODE={sslmode} needs DWS_SSLROOTCERT: the server cannot be verified without a CA. "
            f"Set DWS_SSLROOTCERT_PEM in the repository secrets so the workflow can materialize it."
        )
    settings = {"sslmode": sslmode}
    if sslrootcert:
        settings["sslrootcert"] = sslrootcert
    return settings


def connect():
    """Autocommit connection to the cluster's default database.

    Config is validated before psycopg2 is imported, so a missing variable
    names itself rather than surfacing as an import error.
    """

    host = _require_env("DWS_HOST")
    port = _int_env("DWS_PORT", 8000)
    database = os.getenv("DWS_DATABASE") or "gaussdb"
    user = _require_env("DWS_USERNAME")
    password = _require_env("DWS_PASSWORD")
    timeout = _int_env("DWS_CONNECT_TIMEOUT", 30)
    ssl = ssl_settings()

    import psycopg2

    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=timeout,
            **ssl,
        )
    except psycopg2.Error as exc:
        # Name the sslmode: a TLS refusal and a wrong password fail identically.
        raise DatabaseError(f"Cannot connect to {host}:{port}/{database} with sslmode={ssl['sslmode']}: {exc}") from exc
    # CREATE DATABASE cannot run inside a transaction block.
    connection.autocommit = True
    return connection


def existing_mode(cursor, name: str) -> str | None:
    """The database's compatibility mode, or None when it does not exist."""

    cursor.execute("select datcompatibility from pg_database where datname = %s", (name,))
    row = cursor.fetchone()
    return row[0] if row else None


def create_databases(connection) -> int:
    import psycopg2

    created = 0
    with connection.cursor() as cursor:
        for name, mode in COMPATIBILITY_MODES.items():
            try:
                current = existing_mode(cursor, name)
            except psycopg2.Error as exc:
                raise DatabaseError(f"Cannot read pg_database: {exc}") from exc
            if current is not None:
                if current != mode:
                    raise DatabaseError(
                        f"Database {name} already exists with DBCOMPATIBILITY {current!r}, "
                        f"expected {mode!r}. The mode is fixed at creation, so this "
                        f"cluster cannot serve the {mode} leg; drop the database or use "
                        f"a fresh cluster."
                    )
                print(f"{name}: already present ({mode})", flush=True)
                continue
            try:
                # Module constant, never user input; DDL takes no bound params.
                cursor.execute(f"CREATE DATABASE \"{name}\" DBCOMPATIBILITY '{mode}'")
            except psycopg2.Error as exc:
                raise DatabaseError(f"Cannot create {name} with DBCOMPATIBILITY {mode!r}: {exc}") from exc
            print(f"{name}: created ({mode})", flush=True)
            created += 1
    return created


def cmd_create(_args: argparse.Namespace) -> int:
    connection = connect()
    try:
        created = create_databases(connection)
    finally:
        connection.close()
    print(f"created {created} database(s)", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create the TD and MYSQL databases if absent")
    create.set_defaults(func=cmd_create)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except DatabaseError as exc:
        print(f"::error::{exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
