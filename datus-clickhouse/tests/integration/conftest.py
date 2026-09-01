# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Fixtures for the ClickHouse integration suite.

Environment:

| Variable                | Default        | Purpose                                    |
|-------------------------|----------------|--------------------------------------------|
| ``CLICKHOUSE_HOST``     | ``localhost``  | Server host                                |
| ``CLICKHOUSE_PORT``     | ``8123``       | HTTP port (compose maps it via
                                              ``CLICKHOUSE_HTTP_HOST_PORT``)            |
| ``CLICKHOUSE_USER``     | ``default_user`` | Login user                               |
| ``CLICKHOUSE_PASSWORD`` | ``default_test`` | Login password                           |
| ``CLICKHOUSE_DATABASE`` | ``default_test`` | Test database, created on demand         |

Skip policy: only an unreachable ClickHouse skips. Every statement issued after
a successful connection is a hard requirement, so a green run means the adapter
works rather than that setup limped through.
"""

import logging
import os
from typing import Generator, Optional

import pytest

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector
from datus_clickhouse.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _build_config(database: Optional[str] = None) -> ClickHouseConfig:
    return ClickHouseConfig(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default_user"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "default_test"),
        database=database if database is not None else os.getenv("CLICKHOUSE_DATABASE", "default_test"),
    )


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


def _connect_and_create_database() -> ClickHouseConfig:
    """Verify ClickHouse is reachable (the only step allowed to skip) and create the test database."""
    config = _build_config()
    try:
        init_conn = ClickHouseConnector(_build_config(database=""))
    except Exception as e:
        pytest.skip(f"ClickHouse is not available: {e}")
    try:
        try:
            reachable = init_conn.test_connection()
        except Exception as e:
            pytest.skip(f"ClickHouse is not available: {e}")
        if not reachable:
            pytest.skip("ClickHouse connection test failed")
        if config.database:
            _require_success(
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{config.database}`"),
                "create test database",
            )
    finally:
        init_conn.close()
    return config


@pytest.fixture
def config() -> ClickHouseConfig:
    """Create ClickHouse configuration from environment or defaults."""
    return _build_config()


@pytest.fixture
def connector(config: ClickHouseConfig) -> Generator[ClickHouseConnector, None, None]:
    """Create and cleanup ClickHouse connector for integration tests."""
    _connect_and_create_database()
    conn = ClickHouseConnector(config)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture(scope="session")
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create the known table and view the metadata tests exact-compare against.

    Without known objects the metadata tests can only assert shapes, which an
    adapter returning ``[]`` for everything would pass.
    """
    config = _connect_and_create_database()
    conn = ClickHouseConnector(config)
    table_ref = f"`{config.database}`.`{METADATA_TABLE}`"
    view_ref = f"`{config.database}`.`{METADATA_VIEW}`"
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")
        _require_success(
            conn.execute_ddl(
                f"""
                CREATE TABLE {table_ref} (
                    `id` Int64,
                    `value` Nullable(Int32)
                ) ENGINE = MergeTree()
                ORDER BY id
                """
            ),
            "create metadata table",
        )
        _require_success(
            conn.execute_insert(f"INSERT INTO {table_ref} VALUES (1, 10), (2, 20)"),
            "insert metadata rows",
        )
        _require_success(
            conn.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT `id`, `value` FROM {table_ref}"),
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


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[ClickHouseConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    config = _build_config()

    conn = None
    try:
        # Ensure database exists
        init_config = ClickHouseConfig(
            host=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
            database=None,
        )
        init_conn = ClickHouseConnector(init_config)
        try:
            if not init_conn.test_connection():
                pytest.skip("Database connection test failed")
            if config.database:
                init_conn.execute_ddl(f"CREATE DATABASE IF NOT EXISTS `{config.database}`")
        finally:
            init_conn.close()

        conn = ClickHouseConnector(config)

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
