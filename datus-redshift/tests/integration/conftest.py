# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
from typing import Generator

import pytest
from redshift_connector.error import InterfaceError, OperationalError

from datus_redshift import RedshiftConfig, RedshiftConnector
from datus_redshift.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES", "TPCH_SCHEMA"]


@pytest.fixture
def config() -> RedshiftConfig:
    """Create Redshift configuration from environment variables."""
    return RedshiftConfig(
        host=os.getenv("REDSHIFT_HOST"),
        username=os.getenv("REDSHIFT_USERNAME"),
        password=os.getenv("REDSHIFT_PASSWORD"),
        database=os.getenv("REDSHIFT_DATABASE", "dev"),
        port=int(os.getenv("REDSHIFT_PORT", "5439")),
        schema=os.getenv("REDSHIFT_SCHEMA", "public"),
        ssl=True,
        timeout_seconds=30,
    )


@pytest.fixture
def connector(config: RedshiftConfig) -> Generator[RedshiftConnector, None, None]:
    """Create and cleanup Redshift connector for integration tests."""
    conn = None
    try:
        conn = RedshiftConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except (InterfaceError, OperationalError, OSError) as e:
        # Skip on connection-related errors only (network, auth, DNS).
        # Other exceptions (e.g. programming errors) should propagate
        # so real bugs are not silently masked in CI.
        pytest.skip(f"Database not available: {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ==================== TPC-H Test Data ====================

TPCH_SCHEMA = os.getenv("REDSHIFT_TPCH_SCHEMA", "public")


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[RedshiftConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    redshift_config = RedshiftConfig(
        host=os.getenv("REDSHIFT_HOST"),
        username=os.getenv("REDSHIFT_USERNAME"),
        password=os.getenv("REDSHIFT_PASSWORD"),
        database=os.getenv("REDSHIFT_DATABASE", "dev"),
        port=int(os.getenv("REDSHIFT_PORT", "5439")),
        schema=os.getenv("REDSHIFT_SCHEMA", "public"),
        ssl=True,
        timeout_seconds=30,
    )

    conn = None
    try:
        conn = RedshiftConnector(redshift_config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")

        schema = TPCH_SCHEMA

        # Drop tables first for deterministic setup
        for table in TPCH_TABLES:
            drop_result = conn.execute_ddl(f"DROP TABLE IF EXISTS {schema}.{table}")
            if not drop_result.success:
                pytest.fail(f"Failed to drop table {schema}.{table}: {drop_result.error}")

        # Create tables
        for i, ddl in enumerate(TPCH_DDL):
            ddl_result = conn.execute_ddl(ddl.format(schema=schema))
            if not ddl_result.success:
                pytest.fail(f"Failed to create table {schema}.{TPCH_TABLES[i]}: {ddl_result.error}")

        # Insert data
        for i, data in enumerate(TPCH_DATA):
            ins_result = conn.execute_insert(data.format(schema=schema))
            if not ins_result.success:
                pytest.fail(f"Failed to insert data into {schema}.{TPCH_TABLES[i]}: {ins_result.error}")

    except (InterfaceError, OperationalError, OSError) as e:
        # Skip on connection-related errors only; let other exceptions
        # propagate so real bugs surface during test runs.
        pytest.skip(f"TPC-H setup failed (connection error): {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                schema = TPCH_SCHEMA
                for table in TPCH_TABLES:
                    conn.execute_ddl(f"DROP TABLE IF EXISTS {schema}.{table}")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
