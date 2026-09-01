# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Fixtures for the StarRocks integration suite.

Environment:

| Variable             | Default                             | Purpose                                          |
|----------------------|-------------------------------------|--------------------------------------------------|
| ``STARROCKS_HOST``   | ``localhost``                       | FE host speaking the MySQL protocol              |
| ``STARROCKS_PORT``   | ``9030``                            | FE query port (compose maps ``29030`` in CI)     |
| ``STARROCKS_USER``   | ``root``                            | Login user                                       |
| ``STARROCKS_PASSWORD`` | empty                             | Login password                                   |
| ``STARROCKS_CATALOG``| ``default_catalog``                 | Catalog the tests address                        |
| ``STARROCKS_DATABASE`` | ``test``                          | Test database, created by ``database_setup``     |
| ``HIVE_METASTORE_URI`` | ``thrift://host.docker.internal:9083`` | Metastore backing the external-catalog tests |

Skip policy: only an unreachable StarRocks (or an unreachable Hive metastore,
which the StarRocks compose file does not ship) skips. Every statement issued
after a successful connection is a hard requirement, so a green run means the
adapter works rather than that setup limped through.
"""

import logging
import os
import time
from typing import Generator, Optional

import pytest

from datus_starrocks import StarRocksConfig, StarRocksConnector
from datus_starrocks.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

HIVE_CATALOG_NAME = "hive_test_catalog"
METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"
METADATA_MV = "datus_metadata_mv"

MV_VISIBLE_TIMEOUT_SECONDS = 90

_METADATA_DROP_STATEMENTS = (
    (f"DROP MATERIALIZED VIEW IF EXISTS `{METADATA_MV}`", "drop metadata materialized view"),
    (f"DROP VIEW IF EXISTS `{METADATA_VIEW}`", "drop metadata view"),
    (f"DROP TABLE IF EXISTS `{METADATA_TABLE}`", "drop metadata table"),
)


def _build_config(database: Optional[str] = None) -> StarRocksConfig:
    return StarRocksConfig(
        host=os.getenv("STARROCKS_HOST", "localhost"),
        port=int(os.getenv("STARROCKS_PORT", "9030")),
        username=os.getenv("STARROCKS_USER", "root"),
        password=os.getenv("STARROCKS_PASSWORD", ""),
        catalog=os.getenv("STARROCKS_CATALOG", "default_catalog"),
        database=database if database is not None else os.getenv("STARROCKS_DATABASE", "test"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture(scope="session")
def database_setup() -> Generator[StarRocksConfig, None, None]:
    """Verify StarRocks is reachable and create the test database.

    This is the one place allowed to skip: an unreachable engine. Once the
    connection succeeds, ``CREATE DATABASE`` failing is a real defect.
    """
    test_config = _build_config()
    try:
        init_conn = StarRocksConnector(_build_config(database="information_schema"))
    except Exception as e:
        pytest.skip(f"StarRocks is not available: {e}")

    try:
        try:
            reachable = init_conn.test_connection()
        except Exception as e:
            pytest.skip(f"StarRocks is not available: {e}")
        if not reachable:
            pytest.skip("StarRocks connection test failed")

        if test_config.database:
            _require_success(
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{test_config.database}`"),
                "create test database",
            )
    finally:
        init_conn.close()

    yield test_config


@pytest.fixture
def config(database_setup: StarRocksConfig) -> StarRocksConfig:
    """StarRocks configuration pointing at the prepared test database."""
    return database_setup.model_copy()


@pytest.fixture
def connector(config: StarRocksConfig) -> Generator[StarRocksConnector, None, None]:
    """Live connector for one test."""
    conn = StarRocksConnector(config)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close connector during teardown", exc_info=True)


@pytest.fixture(scope="session")
def hive_catalog_setup(database_setup: StarRocksConfig) -> Generator[str, None, None]:
    """Register a Hive external catalog for the catalog tests.

    StarRocks registers an external catalog lazily, so ``CREATE EXTERNAL
    CATALOG`` succeeds without contacting the metastore — creating it is
    therefore a hard requirement. Listing its databases is what actually needs
    the metastore, and the StarRocks compose file ships none, so that step skips
    rather than fails.
    """
    metastore_uri = os.getenv("HIVE_METASTORE_URI", "thrift://host.docker.internal:9083")
    conn = StarRocksConnector(_build_config(database="information_schema"))
    try:
        _require_success(
            conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`"),
            "drop Hive catalog",
        )
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE EXTERNAL CATALOG `{HIVE_CATALOG_NAME}`
                PROPERTIES (
                    "type" = "hive",
                    "hive.metastore.uris" = "{metastore_uri}"
                )
                """
            ),
            "create Hive catalog",
        )
        try:
            databases = conn.get_databases(catalog_name=HIVE_CATALOG_NAME, include_sys=True)
        except Exception as e:
            pytest.skip(f"Hive metastore {metastore_uri} is not reachable: {e}")
        assert "default" in databases, f"Hive metastore exposes no 'default' database: {databases}"
        yield HIVE_CATALOG_NAME
    finally:
        try:
            conn.execute_ddl(f"DROP CATALOG IF EXISTS `{HIVE_CATALOG_NAME}`")
        except Exception:
            logger.warning("Failed to drop Hive catalog during teardown", exc_info=True)
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close connector during teardown", exc_info=True)


@pytest.fixture(scope="session")
def metadata_objects_setup(database_setup: StarRocksConfig) -> Generator[None, None, None]:
    """Create the table, view, and materialized view metadata tests compare against.

    Without known objects the metadata tests can only assert shapes, which an
    adapter returning ``[]`` for everything would pass.
    """
    conn = StarRocksConnector(database_setup)
    try:
        for statement, operation in _METADATA_DROP_STATEMENTS:
            _require_success(conn.execute_ddl(statement), operation)

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
            conn.execute_ddl(f"CREATE VIEW `{METADATA_VIEW}` AS SELECT `id`, `value` FROM `{METADATA_TABLE}`"),
            "create metadata view",
        )
        # An asynchronous materialized view — the only kind StarRocks records in
        # information_schema.materialized_views. A statement without the REFRESH
        # clause would create a synchronous rollup instead, which no listing
        # returns, and which a PRIMARY KEY base table would reject outright.
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE MATERIALIZED VIEW `{METADATA_MV}`
                DISTRIBUTED BY HASH(`id`) BUCKETS 1
                REFRESH ASYNC
                PROPERTIES ("replication_num" = "1")
                AS SELECT `id`, SUM(`value`) AS total_value
                FROM `{METADATA_TABLE}` GROUP BY `id`
                """
            ),
            "create metadata materialized view",
        )
        _wait_for_materialized_view(conn, database_setup.database or "test")

        yield
    finally:
        try:
            for statement, _ in _METADATA_DROP_STATEMENTS:
                conn.execute_ddl(statement)
        except Exception:
            logger.warning("Failed to drop metadata objects during teardown", exc_info=True)
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close connector during teardown", exc_info=True)


def _wait_for_materialized_view(conn: StarRocksConnector, database: str) -> None:
    """Block until the materialized view is listable.

    ``CREATE MATERIALIZED VIEW`` returns once the definition is accepted; the FE
    publishes it to ``information_schema`` on its own schedule.
    """
    deadline = time.monotonic() + MV_VISIBLE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if METADATA_MV in conn.get_materialized_views(database_name=database):
            return
        time.sleep(2)
    raise AssertionError(
        f"StarRocks materialized view {METADATA_MV} was not listed within {MV_VISIBLE_TIMEOUT_SECONDS} seconds"
    )


@pytest.fixture(scope="session")
def tpch_setup(database_setup: StarRocksConfig) -> Generator[StarRocksConnector, None, None]:
    """Create TPC-H tables, insert data, yield the connector, drop on teardown."""
    conn = StarRocksConnector(database_setup)
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
            logger.warning("Failed to drop TPC-H tables during teardown", exc_info=True)
        try:
            conn.close()
        except Exception:
            logger.warning("Failed to close connection during teardown", exc_info=True)
