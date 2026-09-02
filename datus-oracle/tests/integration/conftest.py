# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
from typing import Generator

import pytest

from datus_oracle import OracleConfig, OracleConnector
from datus_oracle.tpch_data import ROW_COUNTS, TPCH_DATA_BY_TABLE, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA_BY_TABLE", "TPCH_DDL", "TPCH_TABLES"]


def _make_config() -> OracleConfig:
    return OracleConfig(
        host=os.getenv("ORACLE_HOST", "localhost"),
        port=int(os.getenv("ORACLE_PORT", "1521")),
        username=os.getenv("ORACLE_USER", "datus_test"),
        password=os.getenv("ORACLE_PASSWORD", "test_password"),
        service_name=os.getenv("ORACLE_SERVICE_NAME", "FREEPDB1"),
        schema_name=os.getenv("ORACLE_SCHEMA", "DATUS_TEST"),
    )


def drop_table_sql(table_ref: str) -> str:
    """Keep the cleanup compatible with Oracle 19c; swallow ORA-00942 only."""
    return (
        "BEGIN "
        f"EXECUTE IMMEDIATE 'DROP TABLE {table_ref} CASCADE CONSTRAINTS'; "
        "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; "
        "END;"
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> OracleConfig:
    """Create Oracle configuration for integration tests from environment or defaults."""
    return _make_config()


@pytest.fixture
def connector(config: OracleConfig) -> Generator[OracleConnector, None, None]:
    """Create and cleanup Oracle connector for integration tests."""
    try:
        conn = OracleConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def tpch_setup():
    """Set up TPC-H tables and data for integration tests (session-scoped)."""
    cfg = _make_config()

    conn = None
    try:
        conn = OracleConnector(cfg)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    schema_ref = conn.quote_identifier(cfg.schema_name)

    try:
        # Statements are unqualified; the connector applies
        # `ALTER SESSION SET CURRENT_SCHEMA` from cfg.schema_name to each one.
        for i, ddl in enumerate(TPCH_DDL):
            conn.execute_ddl(drop_table_sql(f'{schema_ref}."{TPCH_TABLES[i].upper()}"'))
            result = conn.execute_ddl(ddl)
            assert result.success, f"TPC-H DDL for {TPCH_TABLES[i]} failed: {result.error}"

        # Insert row by row: Oracle has no multi-row VALUES clause.
        for table_name, inserts in zip(TPCH_TABLES, TPCH_DATA_BY_TABLE):
            for insert_sql in inserts:
                result = conn.execute_insert(insert_sql)
                assert result.success, f"TPC-H insert into {table_name} failed: {result.error}"

        yield conn

    finally:
        for table_name in reversed(TPCH_TABLES):
            try:
                conn.execute_ddl(drop_table_sql(f'{schema_ref}."{table_name.upper()}"'))
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass
