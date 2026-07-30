# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import logging
import os
import time
import uuid
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
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture(scope="session")
def database_setup() -> Generator[DorisConfig, None, None]:
    """Verify Doris and create the test database before running integration tests."""
    test_config = _build_config()
    init_conn = DorisConnector(_build_config(database="information_schema"))
    try:
        assert init_conn.test_connection(), "Doris connection test failed"
        if test_config.database:
            _require_success(
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{test_config.database}`"),
                "create test database",
            )
    finally:
        init_conn.close()

    yield test_config


@pytest.fixture
def config(database_setup: DorisConfig) -> DorisConfig:
    return database_setup.model_copy()


@pytest.fixture
def connector(config: DorisConfig) -> Generator[DorisConnector, None, None]:
    conn = DorisConnector(config)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def hive_catalog_setup(
    database_setup: DorisConfig,
) -> Generator[str, None, None]:
    """Create a real Hive external catalog for Doris catalog tests."""
    metastore_uri = os.getenv(
        "HIVE_METASTORE_URI",
        "thrift://hive-metastore:9083",
    )
    conn = DorisConnector(_build_config(database="information_schema"))
    try:
        _require_success(
            conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`"),
            "drop Hive catalog",
        )
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
        assert "default" in conn.get_databases(
            catalog_name=HIVE_CATALOG_NAME,
            include_sys=True,
        )
        yield HIVE_CATALOG_NAME
    finally:
        try:
            conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`")
        finally:
            conn.close()


@pytest.fixture(scope="session")
def metadata_objects_setup(
    database_setup: DorisConfig,
) -> Generator[None, None, None]:
    """Create deterministic table, view, and async materialized-view fixtures."""
    conn = DorisConnector(database_setup)
    try:
        _require_success(
            conn.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS `{METADATA_MV}`"),
            "drop metadata MV",
        )
        _require_success(
            conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`"),
            "drop metadata view",
        )
        _require_success(
            conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`"),
            "drop metadata table",
        )
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
            if METADATA_MV in conn.get_materialized_views(database_name=database_setup.database or "test"):
                break
            time.sleep(2)
        else:
            raise AssertionError("Doris async materialized view did not become visible within 90 seconds")

        yield
    finally:
        try:
            conn.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS `{METADATA_MV}`")
            conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`")
            conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`")
        finally:
            conn.close()


@pytest.fixture
def unique_key_table(
    connector: DorisConnector,
    config: DorisConfig,
) -> Generator[str, None, None]:
    """Create an isolated UNIQUE KEY table for one DML test."""
    table_name = f"datus_dml_{uuid.uuid4().hex[:8]}"
    connector.switch_context(database_name=config.database)
    _require_success(
        connector.execute_ddl(
            f"""
            CREATE TABLE `{table_name}` (
                `id` BIGINT NOT NULL,
                `name` VARCHAR(64)
            ) ENGINE=OLAP
            UNIQUE KEY (`id`)
            DISTRIBUTED BY HASH(`id`) BUCKETS 1
            PROPERTIES (
                "replication_num" = "1",
                "enable_unique_key_merge_on_write" = "true"
            )
            """
        ),
        "create DML test table",
    )
    try:
        yield table_name
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS `{table_name}`")


@pytest.fixture(scope="session")
def tpch_setup(
    database_setup: DorisConfig,
) -> Generator[DorisConnector, None, None]:
    """Create deterministic TPC-H tables and rows for query-contract tests."""
    conn = DorisConnector(database_setup)
    try:
        for table in TPCH_TABLES:
            _require_success(
                conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`"),
                f"drop TPC-H table {table}",
            )
        for index, ddl in enumerate(TPCH_DDL):
            _require_success(conn.execute_ddl(ddl), f"create TPC-H table {index}")
        for index, data in enumerate(TPCH_DATA):
            _require_success(conn.execute_insert(data), f"insert TPC-H rows {index}")

        yield conn
    finally:
        try:
            for table in TPCH_TABLES:
                conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`")
        except Exception:
            logger.warning(
                "Failed to drop TPC-H tables during teardown",
                exc_info=True,
            )
        finally:
            conn.close()
