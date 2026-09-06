#!/usr/bin/env python3

"""Create the compatibility databases a fresh DWS cluster does not ship with.

DWS picks a SQL dialect per database through `DBCOMPATIBILITY`, fixed at
creation time and not alterable afterwards. A new cluster holds only its default
database, which is ORA, so the TD and MYSQL modes have no target until this runs.

The names match what `.github/workflows/dws-cloud-tests.yml` derives for its
non-ORA legs (`datus_ci_<mode>`); ORA reuses the cluster's own default database
rather than a fourth one, since it would be an identical copy.

Configuration comes from the environment, the same variables the tests use:

  DWS_HOST / DWS_PORT / DWS_DATABASE / DWS_USERNAME / DWS_PASSWORD

Existing databases are left alone, but their `datcompatibility` is verified —
a database that exists in the wrong mode would otherwise make the tests report
a dialect mismatch far from its cause.
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
    """TLS options for the connection: encrypted always, verified when possible.

    This carries the cluster admin password to an ephemeral cluster's EIP,
    across the public internet, so libpq's own default is unusable: `prefer`
    falls back to plaintext when the server offers no TLS, and an attacker can
    provoke that fallback by stripping it. `require` never falls back and needs
    no configuration, which is why it is the default here.

    It does not establish *who* answered, so a CA is still worth having: set
    DWS_SSLROOTCERT (the workflow writes it from DWS_SSLROOTCERT_PEM) and this
    rises to `verify-ca` on its own. That is the stronger setting, and the
    trade-off — an unattended cluster holding nothing but freshly loaded test
    fixtures, versus a certificate to distribute and rotate — is a deliberate
    one, not an oversight.
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
    """Open an autocommit connection to the cluster's default database.

    Validated before importing psycopg2 so a missing variable names itself
    instead of surfacing as an import error.
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
        # Surfaces as ::error:: with the sslmode named, since a TLS refusal and
        # a wrong password look nothing alike but fail at the same call.
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
                # The name is a module constant, never user input, so
                # interpolating it is safe — and DDL cannot take a bound
                # parameter here anyway.
                cursor.execute(f"CREATE DATABASE \"{name}\" DBCOMPATIBILITY '{mode}'")
            except psycopg2.Error as exc:
                # A quota, permission or unsupported-mode rejection should name
                # itself, not arrive as a traceback the job log buries.
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
