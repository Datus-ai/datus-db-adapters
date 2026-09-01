# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
from typing import Generator

import pytest

from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector
from datus_postgresql.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> PostgreSQLConfig:
    """Create PostgreSQL configuration for integration tests from environment or defaults."""
    return PostgreSQLConfig(
        host=os.getenv("POSTGRESQL_HOST", "localhost"),
        port=int(os.getenv("POSTGRESQL_PORT", "5432")),
        username=os.getenv("POSTGRESQL_USER", "test_user"),
        password=os.getenv("POSTGRESQL_PASSWORD", "test_password"),
        database=os.getenv("POSTGRESQL_DATABASE", "test"),
        schema_name=os.getenv("POSTGRESQL_SCHEMA", "public"),
    )


@pytest.fixture
def connector(config: PostgreSQLConfig) -> Generator[PostgreSQLConnector, None, None]:
    """Create and cleanup PostgreSQL connector for integration tests."""
    conn = None
    try:
        conn = PostgreSQLConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
        yield conn
    except Exception as e:
        pytest.skip(f"Database not available: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture(scope="session")
def tpch_setup():
    """Set up TPC-H tables and data for integration tests (session-scoped).

    Creates tables, inserts sample data, yields for tests, then drops tables.
    Skips all tests if the database is not available.
    """
    cfg = PostgreSQLConfig(
        host=os.getenv("POSTGRESQL_HOST", "localhost"),
        port=int(os.getenv("POSTGRESQL_PORT", "5432")),
        username=os.getenv("POSTGRESQL_USER", "test_user"),
        password=os.getenv("POSTGRESQL_PASSWORD", "test_password"),
        database=os.getenv("POSTGRESQL_DATABASE", "test"),
        schema_name=os.getenv("POSTGRESQL_SCHEMA", "public"),
    )

    conn = None
    try:
        conn = PostgreSQLConnector(cfg)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    schema = cfg.schema_name

    try:
        # Statements are unqualified; the connector applies `SET search_path`
        # from cfg.schema_name to every statement.
        for table in TPCH_TABLES:
            conn.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE')

        for i, ddl in enumerate(TPCH_DDL):
            result = conn.execute_ddl(ddl)
            if not result.success:
                pytest.fail(f"Failed to create {TPCH_TABLES[i]}: {result.error}")

        for i, insert_sql in enumerate(TPCH_DATA):
            result = conn.execute_insert(insert_sql)
            if not result.success:
                pytest.fail(f"Failed to load {TPCH_TABLES[i]}: {result.error}")

        yield conn

    finally:
        # Cleanup: drop tables in reverse order (foreign key safety)
        for table in reversed(TPCH_TABLES):
            try:
                conn.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE')
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
