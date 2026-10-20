# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Fixtures for the Trino integration suite.

Environment:

| Variable            | Default     | Purpose                                        |
|---------------------|-------------|------------------------------------------------|
| ``TRINO_HOST``      | ``localhost`` | Coordinator host                             |
| ``TRINO_PORT``      | ``8080``    | Coordinator HTTP port                          |
| ``TRINO_USER``      | ``trino``   | Login user                                     |
| ``TRINO_PASSWORD``  | empty       | Login password                                 |
| ``TRINO_CATALOG``   | ``memory``  | Writable catalog the metadata fixtures live in |
| ``TRINO_SCHEMA``    | ``default`` | Schema the metadata fixtures live in           |
| ``TRINO_HTTP_SCHEME`` | ``http`` | HTTP or HTTPS |
| ``TRINO_VERIFY`` | ``true`` | Verify HTTPS certificates |
| ``TRINO_CA_CERT`` | empty | PEM CA bundle file |
| ``TRINO_REQUIRED`` | empty | Fail instead of skipping an unavailable target |

Skip policy: only an unreachable Trino skips. Every statement issued after a
successful connection is a hard requirement, so a green run means the adapter
works rather than that setup limped through.
"""

import logging
import os
from typing import Generator

import pytest

from datus_trino import TrinoConfig, TrinoConnector
from datus_trino.testing import config_from_env

logger = logging.getLogger(__name__)

# Where the metadata fixtures live. Trino's catalogs differ in what they allow:
# CI points TRINO_CATALOG at `tpch`, a read-only generator that rejects CREATE
# TABLE outright, so the writable objects must be pinned to a catalog that
# accepts them rather than following the configured one.
WRITABLE_CATALOG = os.getenv("TRINO_WRITABLE_CATALOG", "memory")
WRITABLE_SCHEMA = os.getenv("TRINO_WRITABLE_SCHEMA", "default")

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _build_config() -> TrinoConfig:
    return config_from_env()


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


def _drop_all(conn, statements: list[tuple[str, str]]) -> None:
    """Run every cleanup statement, then report any that failed.

    Each drop is attempted even when an earlier one fails, so one stuck object
    cannot strand the rest. execute_ddl reports failure through its result
    rather than by raising, so the result is what gets checked.
    """
    failures = []
    for statement, operation in statements:
        try:
            result = conn.execute_ddl(statement)
            if not result.success:
                failures.append(f"{operation}: {result.error}")
        except Exception as exc:  # noqa: BLE001 - reported below, not swallowed
            failures.append(f"{operation}: {exc}")
    if failures:
        logger.warning("Metadata teardown left objects behind: %s", "; ".join(failures))


@pytest.fixture
def config() -> TrinoConfig:
    """Create Trino configuration from environment or defaults for integration tests."""
    return _build_config()


def _connect_or_skip(config: TrinoConfig) -> TrinoConnector:
    conn = TrinoConnector(config)
    try:
        if conn.test_connection():
            return conn
    except Exception:
        conn.close()
        if os.getenv("TRINO_REQUIRED") == "1":
            raise
        pytest.skip("Trino is not available")
    conn.close()
    if os.getenv("TRINO_REQUIRED") == "1":
        pytest.fail("Required Trino connection failed")
    pytest.skip("Trino is not available")


@pytest.fixture
def connector(config: TrinoConfig) -> Generator[TrinoConnector, None, None]:
    conn = _connect_or_skip(config)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create the known table and view the metadata tests exact-compare against.

    The objects live in ``WRITABLE_CATALOG`` (``memory`` by default), which
    supports ``CREATE TABLE``, ``INSERT`` and ``CREATE VIEW``. They deliberately
    do not follow ``TRINO_CATALOG``: CI sets that to ``tpch``, whose connector
    answers "This connector does not support creating tables". Without known
    objects the metadata tests could only assert shapes, which an adapter
    returning ``[]`` for everything would pass.
    """
    test_config = _build_config()
    conn = _connect_or_skip(test_config)

    quoted = f'"{WRITABLE_CATALOG}"."{WRITABLE_SCHEMA}"'
    table_ref = f'{quoted}."{METADATA_TABLE}"'
    view_ref = f'{quoted}."{METADATA_VIEW}"'
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")
        _require_success(
            conn.execute_ddl(f"CREATE TABLE {table_ref} (id BIGINT, value INTEGER)"),
            "create metadata table",
        )
        _require_success(
            conn.execute_insert(f"INSERT INTO {table_ref} VALUES (1, 10), (2, 20)"),
            "insert metadata rows",
        )
        _require_success(
            conn.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT id, value FROM {table_ref}"),
            "create metadata view",
        )
        yield
    finally:
        try:
            _drop_all(
                conn,
                [
                    (f"DROP VIEW IF EXISTS {view_ref}", "drop metadata view"),
                    (f"DROP TABLE IF EXISTS {table_ref}", "drop metadata table"),
                ],
            )
        finally:
            conn.close()


@pytest.fixture
def tpch_connector(config: TrinoConfig) -> Generator[TrinoConnector, None, None]:
    """Create connector pointing to tpch catalog for TPC-H tests."""
    tpch_config = config.model_copy(update={"catalog": "tpch", "schema_name": "tiny"})
    conn = _connect_or_skip(tpch_config)
    try:
        yield conn
    finally:
        conn.close()
