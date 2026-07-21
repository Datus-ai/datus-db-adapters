# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import logging
import os
import time
from typing import Generator

import pytest

from datus_doris import DorisConfig, DorisConnector
from datus_doris.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

HIVE_CATALOG_NAME = "hive_test_catalog"
METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"
METADATA_MV = "datus_metadata_mv"


def _build_config(database: str | None = None) -> DorisConfig:
    return DorisConfig(
        host=os.getenv("DORIS_HOST", "localhost"),
        port=int(os.getenv("DORIS_PORT", "9030")),
        username=os.getenv("DORIS_USER", "root"),
        password=os.getenv("DORIS_PASSWORD", ""),
        catalog=os.getenv("DORIS_CATALOG", "internal"),
        database=database if database is not None else os.getenv("DORIS_DATABASE", "test"),
    )


def _require_success(result, operation: str) -> None:
    if not result.success:
        raise RuntimeError(f"{operation} failed: {result.error}")


@pytest.fixture(scope="session")
def hive_catalog_setup() -> Generator[str, None, None]:
    """Session-scoped fixture: create a Hive external catalog in Doris for catalog tests."""
    metastore_uri = os.getenv("HIVE_METASTORE_URI", "thrift://hive-metastore:9083")
    doris_config = _build_config(database="information_schema")

    conn = None
    try:
        conn = DorisConnector(doris_config)
        if not conn.test_connection():
            pytest.skip("Doris not available for Hive catalog setup")
    except Exception as e:
        pytest.skip(f"Doris not available: {e}")

    try:
        _require_success(conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`"), "drop Hive catalog")
        _require_success(
            conn.execute_ddl(
                f"""
            CREATE CATALOG `{HIVE_CATALOG_NAME}`
            PROPERTIES (
                "type" = "hms",
                "hive.metastore.uris" = "{metastore_uri}"
            )
            """
            ),
            "create Hive catalog",
        )
        databases = conn.get_databases(catalog_name=HIVE_CATALOG_NAME, include_sys=True)
        assert "default" in databases
        yield HIVE_CATALOG_NAME
    finally:
        if conn is not None:
            try:
                conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`")
            except Exception:
                logger.warning("Failed to drop Hive catalog during teardown", exc_info=True)
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture
def config() -> DorisConfig:
    """Create Doris configuration from environment or defaults for integration tests."""
    return _build_config()


@pytest.fixture
def connector(config: DorisConfig) -> Generator[DorisConnector, None, None]:
    """Create and cleanup Doris connector for integration tests."""
    conn = None
    try:
        # Ensure test database exists (connect without database first)
        init_config = DorisConfig(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            catalog=config.catalog,
            database="information_schema",
        )
        init_conn = DorisConnector(init_config)
        try:
            if not init_conn.test_connection():
                pytest.skip("Database connection test failed")
            if config.database:
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{config.database}`")
        finally:
            init_conn.close()

        conn = DorisConnector(config)
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    try:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close connector during teardown", exc_info=True)


@pytest.fixture(scope="session", autouse=True)
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create deterministic table, view, and async materialized view fixtures."""
    test_config = _build_config()
    init_conn = None
    conn = None
    try:
        init_conn = DorisConnector(_build_config(database="information_schema"))
        if not init_conn.test_connection():
            pytest.skip("Database connection test failed")
        _require_success(
            init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{test_config.database}`"),
            "create test database",
        )
        conn = DorisConnector(test_config)
    except Exception as e:
        pytest.skip(f"Database not available: {e}")
    finally:
        if init_conn is not None:
            init_conn.close()

    try:
        _require_success(conn.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS `{METADATA_MV}`"), "drop metadata MV")
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`"), "drop metadata table")
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE TABLE `{METADATA_TABLE}` (
                    `id` BIGINT NOT NULL,
                    `value` INT
                ) ENGINE=OLAP
                DUPLICATE KEY (`id`)
                DISTRIBUTED BY HASH(`id`) BUCKETS 1
                PROPERTIES ("replication_num" = "1")
                """
            ),
            "create metadata table",
        )
        _require_success(
            conn.execute_insert(f"INSERT INTO `{METADATA_TABLE}` VALUES (1, 10), (2, 20)"),
            "insert metadata rows",
        )
        _require_success(
            conn.execute_ddl(f"CREATE VIEW `{METADATA_VIEW}` AS SELECT id, value FROM `{METADATA_TABLE}`"),
            "create metadata view",
        )
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE MATERIALIZED VIEW `{METADATA_MV}`
                BUILD IMMEDIATE REFRESH COMPLETE ON MANUAL
                DISTRIBUTED BY HASH(`id`) BUCKETS 1
                PROPERTIES ("replication_num" = "1")
                AS SELECT id, SUM(value) AS total_value
                FROM `{METADATA_TABLE}` GROUP BY id
                """
            ),
            "create metadata materialized view",
        )

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if METADATA_MV in conn.get_materialized_views(database_name=test_config.database or "test"):
                break
            time.sleep(2)
        else:
            raise AssertionError("Doris async materialized view did not become visible within 90 seconds")

        yield
    finally:
        if conn is not None:
            try:
                conn.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS `{METADATA_MV}`")
                conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`")
                conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`")
            finally:
                conn.close()


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[DorisConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    tpch_config = _build_config()

    conn = None
    # Only skip on connection failures; DDL/DML errors should propagate and fail
    # the suite so they are not silently hidden.
    try:
        # Ensure test database exists
        init_config = DorisConfig(
            host=tpch_config.host,
            port=tpch_config.port,
            username=tpch_config.username,
            password=tpch_config.password,
            catalog=tpch_config.catalog,
            database="information_schema",
        )
        init_conn = DorisConnector(init_config)
        try:
            if not init_conn.test_connection():
                pytest.skip("Database connection test failed")
            if tpch_config.database:
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{tpch_config.database}`")
        finally:
            init_conn.close()

        conn = DorisConnector(tpch_config)
    except Exception as e:
        pytest.skip(f"Database not available: {e}")

    try:
        # Drop tables first for deterministic setup.
        # Errors here are real failures and must not be swallowed.
        for table in TPCH_TABLES:
            conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")

        # Create tables
        for ddl in TPCH_DDL:
            conn.execute_ddl(ddl)

        # Insert data
        for data in TPCH_DATA:
            conn.execute_insert(data)

        yield conn
    finally:
        if conn is not None:
            try:
                for table in TPCH_TABLES:
                    conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
            except Exception:
                logger.warning("Failed to drop TPC-H tables during teardown", exc_info=True)
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close connection during teardown", exc_info=True)
