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
| ``TRINO_HTTP_SCHEME`` | ``http``  | HTTP scheme                                    |

Skip policy: only an unreachable Trino skips. Every statement issued after a
successful connection is a hard requirement, so a green run means the adapter
works rather than that setup limped through.
"""

import logging
import os
from typing import Generator

import pytest

from datus_trino import TrinoConfig, TrinoConnector

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
    return TrinoConfig(
        host=os.getenv("TRINO_HOST", "localhost"),
        port=int(os.getenv("TRINO_PORT", "8080")),
        username=os.getenv("TRINO_USER", "trino"),
        password=os.getenv("TRINO_PASSWORD", ""),
        catalog=os.getenv("TRINO_CATALOG", "memory"),
        schema_name=os.getenv("TRINO_SCHEMA", "default"),
        http_scheme=os.getenv("TRINO_HTTP_SCHEME", "http"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture
def config() -> TrinoConfig:
    """Create Trino configuration from environment or defaults for integration tests."""
    return _build_config()


@pytest.fixture
def connector(config: TrinoConfig) -> Generator[TrinoConnector, None, None]:
    """Create and cleanup Trino connector for integration tests."""
    conn = None
    try:
        conn = TrinoConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as e:
        pytest.skip(f"Database not available: {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
    try:
        conn = TrinoConnector(test_config)
        reachable = conn.test_connection()
    except Exception as e:
        pytest.skip(f"Trino is not available: {e}")
    if not reachable:
        pytest.skip("Trino connection test failed")

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
            conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}")
            conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}")
        except Exception:
            logger.warning("Failed to drop metadata objects during teardown", exc_info=True)
        finally:
            conn.close()


@pytest.fixture
def tpch_connector(config: TrinoConfig) -> Generator[TrinoConnector, None, None]:
    """Create connector pointing to tpch catalog for TPC-H tests."""
    tpch_config = TrinoConfig(
        host=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
        catalog="tpch",
        schema_name="tiny",
        http_scheme=config.http_scheme,
        verify=config.verify,
        timeout_seconds=config.timeout_seconds,
    )
    conn = None
    try:
        conn = TrinoConnector(tpch_config)
        if not conn.test_connection():
            pytest.skip("TPC-H connection test failed")
    except Exception as e:
        pytest.skip(f"TPC-H catalog not available: {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
