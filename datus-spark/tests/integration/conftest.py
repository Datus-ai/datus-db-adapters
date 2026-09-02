# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import logging
import os
from typing import Generator

import pytest

from datus_spark import SparkConfig, SparkConnector
from datus_spark.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _build_config() -> SparkConfig:
    return SparkConfig(
        host=os.getenv("SPARK_HOST", "localhost"),
        port=int(os.getenv("SPARK_PORT", "10000")),
        username=os.getenv("SPARK_USER", "spark"),
        password=os.getenv("SPARK_PASSWORD", ""),
        database=os.getenv("SPARK_DATABASE", "default"),
        auth_mechanism=os.getenv("SPARK_AUTH_MECHANISM", "NONE"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture
def config() -> SparkConfig:
    """Create Spark configuration from environment or defaults for integration tests."""
    return _build_config()


@pytest.fixture(scope="session")
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create the known table and view the metadata tests exact-compare against.

    Without known objects the metadata tests can only assert shapes, which an
    adapter returning ``[]`` for everything would pass. Only an unreachable
    Spark skips; every statement after a successful connection is a hard
    requirement.
    """
    test_config = _build_config()
    try:
        conn = SparkConnector(test_config)
        reachable = conn.test_connection()
    except Exception as e:
        pytest.skip(f"Spark is not available: {e}")
    if not reachable:
        pytest.skip("Spark connection test failed")

    database = test_config.database or "default"
    table_ref = f"`{database}`.`{METADATA_TABLE}`"
    view_ref = f"`{database}`.`{METADATA_VIEW}`"
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")
        _require_success(
            conn.execute_ddl(f"CREATE TABLE {table_ref} (id BIGINT, value INT) USING parquet"),
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
def connector(config: SparkConfig) -> Generator[SparkConnector, None, None]:
    """Create and cleanup Spark connector for integration tests."""
    conn = None
    try:
        conn = SparkConnector(config)
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
def tpch_setup():
    """Create TPC-H tables with sample data for integration tests (session-scoped)."""
    config = SparkConfig(
        host=os.getenv("SPARK_HOST", "localhost"),
        port=int(os.getenv("SPARK_PORT", "10000")),
        username=os.getenv("SPARK_USER", "spark"),
        password=os.getenv("SPARK_PASSWORD", ""),
        database=os.getenv("SPARK_DATABASE", "default"),
        auth_mechanism=os.getenv("SPARK_AUTH_MECHANISM", "NONE"),
    )
    conn = None
    try:
        conn = SparkConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed for TPC-H setup")

        # Drop tables first for deterministic setup
        for table in TPCH_TABLES:
            conn.execute_ddl(f"DROP TABLE IF EXISTS `default`.`{table}`")

        # Create tables and insert data
        for ddl in TPCH_DDL:
            conn.execute_ddl(ddl)
        for data in TPCH_DATA:
            conn.execute_ddl(data)
    except Exception as e:
        pytest.skip(f"TPC-H setup failed: {e}")
    else:
        yield conn
    finally:
        # Cleanup: drop all TPC-H tables
        if conn is not None:
            for table in TPCH_TABLES:
                try:
                    conn.execute_ddl(f"DROP TABLE IF EXISTS `default`.`{table}`")
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
