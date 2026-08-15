# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Fixtures for the GaussDB / openGauss integration suite.

Connection settings come from the environment:

===========================  ==================  =========================
Variable                     Default             Meaning
===========================  ==================  =========================
``GAUSSDB_HOST``             ``127.0.0.1``       server host
``GAUSSDB_PORT``             ``25434``           server port
``GAUSSDB_USER``             ``datus``           login user
``GAUSSDB_PASSWORD``         ``Datus@123``       login password
``GAUSSDB_DATABASE``         ``postgres``        default database
``GAUSSDB_SCHEMA``           ``public``          default schema
``GAUSSDB_DRIVER``           platform default    ``gaussdb``, ``pg8000`` or ``psycopg2``
===========================  ==================  =========================

IMPORTANT — platform caveat: the official ``gaussdb`` driver binds the
GaussDB/openGauss build of libpq via ctypes, and only that build; there is no
macOS distribution of it. Run this suite inside a Linux container (the same
one that runs the openGauss server is fine)::

    docker run --rm -v "$PWD:/w" -w /w --network host python:3.12 \\
        bash -c "pip install -e datus-db-core -e datus-sqlalchemy \\
                 -e datus-postgresql -e datus-gaussdb pytest && \\
                 pytest datus-gaussdb/tests/integration -m integration"

Every fixture skips (never fails) when the server is unreachable, so the suite
is inert on developer machines without a GaussDB instance.

Off Linux, tests using the official driver are skipped up front: the driver
binds whatever libpq it can find, and a vanilla PostgreSQL libpq makes it
*segfault* on connect rather than raise — a crash no fixture can catch. The
``psycopg2`` path is safe on macOS and is selected there by default. Set
``GAUSSDB_FORCE_INTEGRATION=1`` only to run the official driver on a host that
really does have the GaussDB client libraries.
"""

import os
import sys
from typing import Generator

import pytest

from datus_gaussdb import GaussDBConfig, GaussDBConnector
from datus_gaussdb.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES


def _require_gaussdb_libpq_platform() -> None:
    """Skip unsafe official-driver runs; pg8000/psycopg2 run anywhere."""
    driver = os.getenv("GAUSSDB_DRIVER") or ("pg8000" if sys.platform == "darwin" else "gaussdb")
    if driver == "gaussdb" and sys.platform != "linux" and os.getenv("GAUSSDB_FORCE_INTEGRATION") != "1":
        pytest.skip(
            f"the gaussdb driver needs the GaussDB/openGauss libpq, which has no {sys.platform} build; "
            "run this suite in a Linux container (or set GAUSSDB_FORCE_INTEGRATION=1)"
        )


@pytest.fixture(autouse=True, scope="session")
def require_gaussdb_libpq_platform():
    """Guard the whole session before any fixture opens a connection."""
    _require_gaussdb_libpq_platform()


def _build_config() -> GaussDBConfig:
    return GaussDBConfig(
        host=os.getenv("GAUSSDB_HOST", "127.0.0.1"),
        port=int(os.getenv("GAUSSDB_PORT", "25434")),
        username=os.getenv("GAUSSDB_USER", "datus"),
        password=os.getenv("GAUSSDB_PASSWORD", "Datus@123"),
        database=os.getenv("GAUSSDB_DATABASE", "postgres"),
        schema_name=os.getenv("GAUSSDB_SCHEMA", "public"),
        driver=os.getenv("GAUSSDB_DRIVER") or ("pg8000" if sys.platform == "darwin" else "gaussdb"),
    )


@pytest.fixture
def config() -> GaussDBConfig:
    """GaussDB configuration built from the environment."""
    return _build_config()


@pytest.fixture
def connector(config: GaussDBConfig) -> Generator[GaussDBConnector, None, None]:
    """Live connector; skips the test when no GaussDB instance is reachable."""
    conn = None
    try:
        conn = GaussDBConnector(config)
        if not conn.test_connection():
            pytest.skip("GaussDB connection test failed")
    except Exception as e:
        pytest.skip(f"GaussDB not available: {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture
def compat_mode(connector: GaussDBConnector) -> str:
    """Probed database compatibility mode ('A', 'B' or 'PG')."""
    return connector._get_traits().compat_mode


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[GaussDBConnector, None, None]:
    """Session-scoped TPC-H dataset: create tables, load rows, drop on teardown."""
    _require_gaussdb_libpq_platform()
    conn = None
    try:
        # Only an unreachable server is a skip; provisioning failures are real
        # regressions and must fail the suite instead of silently dropping it.
        try:
            conn = GaussDBConnector(_build_config())
            reachable = conn.test_connection()
        except Exception as e:
            pytest.skip(f"GaussDB is unavailable for TPC-H setup: {e}")
        if not reachable:
            pytest.skip("GaussDB connection test failed for TPC-H setup")

        for table in TPCH_TABLES:
            conn.execute_ddl(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        for ddl in TPCH_DDL:
            result = conn.execute_ddl(ddl)
            if not result.success:
                raise RuntimeError(f"TPC-H DDL failed: {result.error}")
        for insert_sql in TPCH_DATA:
            result = conn.execute_insert(insert_sql)
            if not result.success:
                raise RuntimeError(f"TPC-H insert failed: {result.error}")

        yield conn
    finally:
        if conn is not None:
            for table in TPCH_TABLES:
                try:
                    conn.execute_ddl(f'DROP TABLE IF EXISTS "{table}" CASCADE')
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
