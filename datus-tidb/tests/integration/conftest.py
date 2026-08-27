# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import logging
import os
import time
import uuid
from typing import Generator

import pytest

from datus_tidb import TiDBConfig, TiDBConnector
from datus_tidb.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"

# A TiFlash replica syncs asynchronously; on the test-sized tables used here it
# lands in a couple of seconds, but a cold TiFlash store can take longer.
TIFLASH_READY_TIMEOUT = float(os.getenv("TIDB_TIFLASH_READY_TIMEOUT", "120"))


def _build_config(database: str | None = None) -> TiDBConfig:
    return TiDBConfig(
        host=os.getenv("TIDB_HOST", "127.0.0.1"),
        port=int(os.getenv("TIDB_PORT", "4000")),
        username=os.getenv("TIDB_USER", "root"),
        password=os.getenv("TIDB_PASSWORD", ""),
        database=database if database is not None else os.getenv("TIDB_DATABASE", "test"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


def wait_for_tiflash_replica(connector: TiDBConnector, table_name: str, database_name: str = "") -> None:
    """Block until the table's TiFlash replica reports AVAILABLE, or fail."""
    deadline = time.monotonic() + TIFLASH_READY_TIMEOUT
    while time.monotonic() < deadline:
        for replica in connector.get_tiflash_replicas(database_name=database_name):
            if replica["table_name"] == table_name and replica["available"]:
                return
        time.sleep(1)
    raise AssertionError(f"TiFlash replica for {table_name!r} did not become available within {TIFLASH_READY_TIMEOUT}s")


@pytest.fixture(scope="session")
def database_setup() -> Generator[TiDBConfig, None, None]:
    """Verify TiDB and create the test database before running integration tests."""
    test_config = _build_config()
    init_conn = TiDBConnector(_build_config(database="information_schema"))
    try:
        assert init_conn.test_connection(), "TiDB connection test failed"
        if test_config.database:
            _require_success(
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{test_config.database}`"),
                "create test database",
            )
    finally:
        init_conn.close()

    yield test_config


@pytest.fixture
def config(database_setup: TiDBConfig) -> TiDBConfig:
    return database_setup.model_copy()


@pytest.fixture
def connector(config: TiDBConfig) -> Generator[TiDBConnector, None, None]:
    conn = TiDBConnector(config)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def tiflash_available(database_setup: TiDBConfig) -> bool:
    """Whether the cluster has a live TiFlash store.

    The columnar tests are meaningless without one, and a single-container
    `--store=unistore` TiDB has none, so they skip rather than fail there.
    """
    conn = TiDBConnector(database_setup)
    try:
        result = conn.execute(
            {
                "sql_query": (
                    "SELECT COUNT(*) AS store_count FROM information_schema.TIKV_STORE_STATUS "
                    "WHERE LABEL LIKE '%tiflash%'"
                )
            },
            result_format="list",
        )
        return bool(result.success and result.sql_return and int(result.sql_return[0]["store_count"]) > 0)
    except Exception:
        logger.warning("Could not determine TiFlash availability", exc_info=True)
        return False
    finally:
        conn.close()


@pytest.fixture(scope="session")
def metadata_objects_setup(database_setup: TiDBConfig) -> Generator[None, None, None]:
    """Create a deterministic table and view for metadata tests."""
    conn = TiDBConnector(database_setup)
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`"), "drop metadata table")
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE TABLE `{METADATA_TABLE}` (
                    `id` BIGINT NOT NULL,
                    `value` INT COMMENT 'the value',
                    PRIMARY KEY (`id`)
                )
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
        yield
    finally:
        try:
            conn.execute_ddl(f"DROP VIEW IF EXISTS `{METADATA_VIEW}`")
            conn.execute_ddl(f"DROP TABLE IF EXISTS `{METADATA_TABLE}`")
        finally:
            conn.close()


@pytest.fixture
def temp_table(connector: TiDBConnector) -> Generator[str, None, None]:
    """Create an isolated table for one DML test."""
    table_name = f"datus_dml_{uuid.uuid4().hex[:8]}"
    _require_success(
        connector.execute_ddl(
            f"""
            CREATE TABLE `{table_name}` (
                `id` BIGINT NOT NULL,
                `name` VARCHAR(64),
                PRIMARY KEY (`id`)
            )
            """
        ),
        "create DML test table",
    )
    try:
        yield table_name
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS `{table_name}`")


@pytest.fixture
def columnar_table(
    connector: TiDBConnector,
    config: TiDBConfig,
    tiflash_available: bool,
) -> Generator[str, None, None]:
    """Create a table with a synced TiFlash replica for columnar tests."""
    if not tiflash_available:
        pytest.skip("cluster has no TiFlash store")

    table_name = f"datus_tiflash_{uuid.uuid4().hex[:8]}"
    _require_success(
        connector.execute_ddl(
            f"""
            CREATE TABLE `{table_name}` (
                `id` BIGINT NOT NULL,
                `grp` VARCHAR(16) NOT NULL,
                `amount` DECIMAL(12,2) NOT NULL,
                PRIMARY KEY (`id`)
            )
            """
        ),
        "create columnar test table",
    )
    try:
        rows = ", ".join(f"({i}, 'g{i % 4}', {i * 1.5:.2f})" for i in range(200))
        _require_success(connector.execute_insert(f"INSERT INTO `{table_name}` VALUES {rows}"), "insert columnar rows")
        _require_success(
            connector.execute_ddl(f"ALTER TABLE `{table_name}` SET TIFLASH REPLICA 1"),
            "grant TiFlash replica",
        )
        wait_for_tiflash_replica(connector, table_name, database_name=config.database or "")
        yield table_name
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS `{table_name}`")


@pytest.fixture(scope="session")
def tpch_setup(database_setup: TiDBConfig) -> Generator[TiDBConnector, None, None]:
    """Create deterministic TPC-H tables and rows for query-contract tests."""
    conn = TiDBConnector(database_setup)
    try:
        for table in TPCH_TABLES:
            _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`"), f"drop TPC-H table {table}")
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
            logger.warning("Failed to drop TPC-H tables during teardown", exc_info=True)
        finally:
            conn.close()
