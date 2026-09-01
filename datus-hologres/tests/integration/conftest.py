# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import os
import uuid
from typing import Generator

import pytest

from datus_hologres import HologresConfig, HologresConnector, register
from datus_hologres.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

register()

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES"]


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for live Hologres tests")
    return value


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"


@pytest.fixture(scope="session")
def base_config() -> HologresConfig:
    return HologresConfig(
        host=_required_env("HOLOGRES_HOST"),
        port=int(os.getenv("HOLOGRES_PORT") or "80"),
        access_key_id=_required_env("HOLOGRES_ACCESS_KEY_ID"),
        access_key_secret=_required_env("HOLOGRES_ACCESS_KEY_SECRET"),
        database=_required_env("HOLOGRES_DATABASE"),
        schema=os.getenv("HOLOGRES_SCHEMA") or "public",
        sslmode=os.getenv("HOLOGRES_SSLMODE") or "prefer",
    )


@pytest.fixture(scope="session")
def connector(base_config: HologresConfig) -> Generator[HologresConnector, None, None]:
    admin = HologresConnector(base_config)
    assert admin.test_connection()
    schema_name = f"datus_ci_{uuid.uuid4().hex[:12]}"
    create = admin.execute_ddl(f'CREATE SCHEMA "{schema_name}"')
    _assert_success(create, "create isolated test schema")

    test_connector = HologresConnector(base_config.model_copy(update={"schema_name": schema_name}))
    try:
        assert test_connector.test_connection()
        yield test_connector
    finally:
        test_connector.close()
        cleanup = admin.execute_ddl(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        _assert_success(cleanup, "drop isolated test schema")
        admin.close()


@pytest.fixture(scope="session")
def tpch_setup(connector: HologresConnector) -> HologresConnector:
    """Create and load the shared TPC-H dataset in the session's isolated schema.

    Statements are unqualified; the connector applies `SET search_path` from
    its schema_name to every statement.
    """
    schema = connector.schema_name
    for table_name, ddl in zip(TPCH_TABLES, TPCH_DDL):
        drop = connector.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table_name}" CASCADE')
        _assert_success(drop, f"drop stale {table_name}")
        create = connector.execute_ddl(ddl)
        _assert_success(create, f"create {table_name}")

    for table_name, insert_sql in zip(TPCH_TABLES, TPCH_DATA):
        insert = connector.execute_insert(insert_sql)
        _assert_success(insert, f"load {table_name}")

    return connector
