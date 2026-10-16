# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""MySQL integration environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| MYSQL_HOST/PORT | localhost/3306 | Database endpoint |
| MYSQL_USER/PASSWORD | test_user/test_password | Test identity |
| MYSQL_DATABASE | test | Isolated test database |
| MYSQL_SSL_CA | unset | CA path; enables verified Aurora-style TLS |
| DATUS_TEST_TLS_DIR | unset | Disposable certificates for negative TLS tests |
"""

import os
from typing import Generator

import pytest

from datus_mysql import MySQLConfig, MySQLConnector
from datus_mysql.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES"]


def _build_config() -> MySQLConfig:
    return MySQLConfig(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        username=os.getenv("MYSQL_USER", "test_user"),
        password=os.getenv("MYSQL_PASSWORD", "test_password"),
        database=os.getenv("MYSQL_DATABASE", "test"),
        ssl_ca=os.getenv("MYSQL_SSL_CA"),
        aurora=bool(os.getenv("MYSQL_SSL_CA")),
    )


@pytest.fixture
def config() -> MySQLConfig:
    return _build_config()


@pytest.fixture
def connector(config: MySQLConfig) -> Generator[MySQLConnector, None, None]:
    """Create and cleanup MySQL connector for integration tests."""
    conn = MySQLConnector(config)
    try:
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as e:
        conn.close()
        pytest.skip(f"Database not available: {e}")
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[MySQLConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    tpch_config = _build_config()

    conn = None
    try:
        conn = MySQLConnector(tpch_config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")

    except Exception as e:
        if conn is not None:
            conn.close()
        pytest.skip(f"MySQL unavailable for TPC-H setup: {e}")

    try:
        # Provisioning failures after successful connection must fail the suite.
        for table in TPCH_TABLES:
            result = conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
            if not result.success:
                raise RuntimeError(f"TPC-H cleanup failed: {result.error}")

        # Create tables
        for ddl in TPCH_DDL:
            result = conn.execute_ddl(ddl)
            if not result.success:
                raise RuntimeError(f"TPC-H DDL failed: {result.error}")

        # Insert data
        for data in TPCH_DATA:
            result = conn.execute_insert(data)
            if not result.success:
                raise RuntimeError(f"TPC-H insert failed: {result.error}")
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
