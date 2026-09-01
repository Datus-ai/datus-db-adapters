# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import json
import logging
import os
from typing import Generator

import pytest

from datus_hive import HiveConfig, HiveConnector
from datus_hive.tpch_data import TPCH_DATA, TPCH_DDL, TPCH_TABLES

logger = logging.getLogger(__name__)

METADATA_TABLE = "datus_metadata_table"
METADATA_VIEW = "datus_metadata_view"


def _require_success(result, operation: str) -> None:
    assert result.success, f"{operation} failed: {result.error}"


def _load_configuration() -> dict:
    raw = os.getenv("HIVE_CONFIGURATION_JSON")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.skip(f"Invalid HIVE_CONFIGURATION_JSON: {exc}")
    if not isinstance(data, dict):
        pytest.skip("HIVE_CONFIGURATION_JSON must be a JSON object")
    return data


def _build_hive_config() -> HiveConfig:
    """Build HiveConfig from environment variables."""
    auth = os.getenv("HIVE_AUTH")
    return HiveConfig(
        host=os.getenv("HIVE_HOST", "localhost"),
        port=int(os.getenv("HIVE_PORT", "10000")),
        database=os.getenv("HIVE_DATABASE", "default"),
        username=os.getenv("HIVE_USERNAME", "hive"),
        password=os.getenv("HIVE_PASSWORD", ""),
        auth=auth if auth else None,
        configuration=_load_configuration(),
    )


@pytest.fixture
def config() -> HiveConfig:
    """Create Hive configuration for integration tests from environment or defaults."""
    return _build_hive_config()


@pytest.fixture
def connector(config: HiveConfig) -> Generator[HiveConnector, None, None]:
    """Create and cleanup Hive connector for integration tests."""
    conn = None
    try:
        conn = HiveConnector(config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")
    except Exception as exc:
        pytest.skip(f"Database not available: {exc}")
    try:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture(scope="session")
def metadata_objects_setup() -> Generator[None, None, None]:
    """Create the known table and view the metadata tests exact-compare against.

    Without known objects the metadata tests can only assert shapes, which an
    adapter returning ``[]`` for everything would pass. Only an unreachable
    Hive skips; every statement after a successful connection is a hard
    requirement.
    """
    hive_config = _build_hive_config()
    try:
        conn = HiveConnector(hive_config)
        reachable = conn.test_connection()
    except Exception as exc:
        pytest.skip(f"Hive is not available: {exc}")
    if not reachable:
        pytest.skip("Hive connection test failed")

    database = hive_config.database or "default"
    table_ref = f"`{database}`.`{METADATA_TABLE}`"
    view_ref = f"`{database}`.`{METADATA_VIEW}`"
    try:
        _require_success(conn.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop metadata view")
        _require_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")
        _require_success(
            conn.execute_ddl(f"CREATE TABLE {table_ref} (id INT, value INT) STORED AS ORC"),
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


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[HiveConnector, None, None]:
    """Session-scoped fixture: create TPC-H tables, insert data, yield connector, cleanup."""
    hive_config = _build_hive_config()

    conn = None
    try:
        conn = HiveConnector(hive_config)
        if not conn.test_connection():
            pytest.skip("Database connection test failed")

        # Drop existing tables first to ensure clean state
        for table in TPCH_TABLES:
            conn.execute_ddl(f"DROP TABLE IF EXISTS {table}")

        # Create tables
        for ddl in TPCH_DDL:
            conn.execute_ddl(ddl)

        # Insert data
        for data in TPCH_DATA:
            conn.execute_insert(data)

    except Exception as exc:
        pytest.skip(f"TPC-H setup failed: {exc}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                for table in TPCH_TABLES:
                    conn.execute_ddl(f"DROP TABLE IF EXISTS {table}")
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
