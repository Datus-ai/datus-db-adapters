# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import logging
import os
from typing import Generator

import pytest

from datus_greenplum import GreenplumConfig, GreenplumConnector
from datus_greenplum.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _build_config() -> GreenplumConfig:
    return GreenplumConfig(
        host=os.getenv("GREENPLUM_HOST", "localhost"),
        port=int(os.getenv("GREENPLUM_PORT", "15432")),
        username=os.getenv("GREENPLUM_USER", "gpadmin"),
        password=os.getenv("GREENPLUM_PASSWORD", "pivotal"),
        database=os.getenv("GREENPLUM_DATABASE", "test"),
        schema_name=os.getenv("GREENPLUM_SCHEMA", "public"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture
def config() -> GreenplumConfig:
    """Create Greenplum configuration for integration tests from environment or defaults."""
    return _build_config()


@pytest.fixture(scope="session")
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create the known table and view the metadata tests exact-compare against.

    Without known objects the metadata tests can only assert shapes, which an
    adapter returning ``[]`` for everything would pass. Only an unreachable
    Greenplum skips; every statement after a successful connection is a hard
    requirement.
    """
    test_config = _build_config()
    try:
        conn = GreenplumConnector(test_config)
        reachable = conn.test_connection()
    except Exception as e:
        pytest.skip(f"Greenplum is not available: {e}")
    if not reachable:
        pytest.skip("Greenplum connection test failed")

    schema = test_config.schema_name
    table_ref = f'"{schema}"."{METADATA_TABLE}"'
    view_ref = f'"{schema}"."{METADATA_VIEW}"'
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref} CASCADE"), "drop metadata table")
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE TABLE {table_ref} (
                    id BIGINT NOT NULL,
                    value INTEGER
                ) DISTRIBUTED BY (id)
                """
            ),
            "create metadata table",
        )
        _require_success(
            conn.execute_insert(f"INSERT INTO {table_ref} (id, value) VALUES (1, 10), (2, 20)"),
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
            conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref} CASCADE")
        except Exception:
            logger.warning("Failed to drop metadata objects during teardown", exc_info=True)
        finally:
            conn.close()


@pytest.fixture
def connector(config: GreenplumConfig) -> Generator[GreenplumConnector, None, None]:
    """Create and cleanup Greenplum connector for integration tests."""
    conn = None
    try:
        conn = GreenplumConnector(config)
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
def tpch_setup() -> Generator[GreenplumConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    config = GreenplumConfig(
        host=os.getenv("GREENPLUM_HOST", "localhost"),
        port=int(os.getenv("GREENPLUM_PORT", "15432")),
        username=os.getenv("GREENPLUM_USER", "gpadmin"),
        password=os.getenv("GREENPLUM_PASSWORD", "pivotal"),
        database=os.getenv("GREENPLUM_DATABASE", "test"),
        schema_name=os.getenv("GREENPLUM_SCHEMA", "public"),
    )

    conn = None
    try:
        conn = GreenplumConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed for TPC-H setup")

        # Drop tables first for deterministic setup
        for table in TPCH_TABLES:
            conn.execute_ddl(f'DROP TABLE IF EXISTS "{table}" CASCADE')

        # Create tables
        for ddl in TPCH_DDL:
            conn.execute_ddl(ddl)

        # Insert data
        for data in TPCH_DATA:
            conn.execute_insert(data)

    except Exception as e:
        pytest.skip(f"TPC-H setup failed: {e}")
    else:
        yield conn
    finally:
        # Cleanup: drop all TPC-H tables
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
