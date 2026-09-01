# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
from typing import Generator

import pytest

from datus_mysql import MySQLConfig, MySQLConnector
from datus_mysql.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES"]


@pytest.fixture
def config() -> MySQLConfig:
    """Create MySQL configuration for integration tests from environment or defaults."""
    return MySQLConfig(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        username=os.getenv("MYSQL_USER", "test_user"),
        password=os.getenv("MYSQL_PASSWORD", "test_password"),
        database=os.getenv("MYSQL_DATABASE", "test"),
    )


@pytest.fixture
def connector(config: MySQLConfig) -> Generator[MySQLConnector, None, None]:
    """Create and cleanup MySQL connector for integration tests."""
    try:
        conn = MySQLConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
        yield conn
    except Exception as e:
        pytest.skip(f"Database not available: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[MySQLConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    tpch_config = MySQLConfig(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        username=os.getenv("MYSQL_USER", "test_user"),
        password=os.getenv("MYSQL_PASSWORD", "test_password"),
        database=os.getenv("MYSQL_DATABASE", "test"),
    )

    conn = None
    try:
        conn = MySQLConnector(tpch_config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")

        # Drop tables first for deterministic setup
        for table in TPCH_TABLES:
            conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")

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
        if conn is not None:
            try:
                for table in TPCH_TABLES:
                    conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
