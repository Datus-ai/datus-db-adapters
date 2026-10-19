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
| ``CLICKHOUSE_PROTOCOL`` | ``http``       | HTTP or HTTPS                            |
| ``CLICKHOUSE_VERIFY``   | ``true``       | HTTPS chain and hostname verification    |
| ``CLICKHOUSE_CA_CERT``  | unset          | PEM CA bundle path                       |
| ``CLICKHOUSE_REQUIRED`` | unset          | CI: fail instead of skipping unavailable |
| ``CLICKHOUSE_USER``     | ``default_user`` | Login user                               |
| ``CLICKHOUSE_PASSWORD`` | ``default_test`` | Login password                           |
| ``CLICKHOUSE_DATABASE`` | ``default_test`` | Test database, created on demand         |

Skip policy: only an unreachable ClickHouse skips. Every statement issued after
a successful connection is a hard requirement, so a green run means the adapter
works rather than that setup limped through.
"""

import logging
import os
import sys
from typing import Generator, Optional

import pytest

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector
from datus_clickhouse.testing import config_from_env
from datus_clickhouse.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _build_config(database: Optional[str] = None) -> ClickHouseConfig:
    return config_from_env({"database": database} if database is not None else None)


def _unavailable(message: str):
    if os.getenv("CLICKHOUSE_REQUIRED") == "1" or os.getenv("CLICKHOUSE_PROTOCOL") == "https":
        raise RuntimeError(message)
    pytest.skip(message)


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


def _connect_and_create_database() -> ClickHouseConfig:
    """Verify ClickHouse is reachable (the only step allowed to skip) and create the test database."""
    config = _build_config()
    try:
        init_conn = ClickHouseConnector(_build_config(database=""))
    except Exception as e:
        _unavailable(f"ClickHouse is not available: {e}")
    try:
        try:
            reachable = init_conn.test_connection()
        except Exception as e:
            _unavailable(f"ClickHouse is not available: {e}")
        if not reachable:
            _unavailable("ClickHouse connection test failed")
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
    config = _connect_and_create_database()
    conn = ClickHouseConnector(config)
    try:
        for table in TPCH_TABLES:
            _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`"), "drop TPC-H table")
        for ddl in TPCH_DDL:
            _require_success(conn.execute_ddl(ddl), "create TPC-H table")
        for data in TPCH_DATA:
            _require_success(conn.execute_insert(data), "insert TPC-H rows")
        yield conn
    finally:
        original = sys.exception()
        try:
            for table in TPCH_TABLES:
                _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS `{table}`"), "cleanup TPC-H table")
        except Exception as error:
            if original is None:
                raise
            original.add_note(f"TPC-H cleanup also failed: {error}")
        finally:
            conn.close()
