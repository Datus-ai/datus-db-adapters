# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Fixtures for live DWS integration tests.

Every test object lands in one run-scoped schema that is dropped on teardown,
so concurrent CI runs against the same cluster cannot collide.
"""

import os
import uuid
from typing import Generator

import pytest

from datus_dws import DWSConfig, DWSConnector, register

register()

# Covers every storage and distribution form the adapter claims to represent.
TABLE_DDL = {
    "t_row_hash": """
        CREATE TABLE "{schema}"."t_row_hash" (
            id INTEGER NOT NULL,
            name VARCHAR(64) DEFAULT 'anon',
            amt NUMERIC(10,2)
        ) WITH (orientation=row) DISTRIBUTE BY HASH (id)
    """,
    "t_col_compress": """
        CREATE TABLE "{schema}"."t_col_compress" (
            id INTEGER,
            name VARCHAR(64),
            amt NUMERIC(10,2)
        ) WITH (orientation=column, compression=middle) DISTRIBUTE BY HASH (id)
    """,
    "t_replication": """
        CREATE TABLE "{schema}"."t_replication" (
            id INTEGER,
            name VARCHAR(64)
        ) DISTRIBUTE BY REPLICATION
    """,
    "t_roundrobin": """
        CREATE TABLE "{schema}"."t_roundrobin" (
            id INTEGER,
            name VARCHAR(64)
        ) DISTRIBUTE BY ROUNDROBIN
    """,
    "t_partitioned": """
        CREATE TABLE "{schema}"."t_partitioned" (
            id INTEGER,
            dt DATE
        ) DISTRIBUTE BY HASH (id)
        PARTITION BY RANGE (dt) (
            PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
            PARTITION pmax VALUES LESS THAN (MAXVALUE)
        )
    """,
}

TABLE_DATA = {
    "t_row_hash": [(1, "alpha", 1.50), (2, "beta", 2.50), (3, "gamma", 3.50)],
    "t_col_compress": [(1, "alpha", 1.50), (2, "beta", 2.50)],
    "t_replication": [(1, "alpha"), (2, "beta")],
    "t_roundrobin": [(1, "alpha"), (2, "beta")],
}

VIEW_DDL = 'CREATE VIEW "{schema}"."v_rows" AS SELECT id, name FROM "{schema}"."t_row_hash"'


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for live DWS tests")
    return value


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def _run_scope() -> str:
    """A schema suffix unique per CI run, falling back to a random one locally."""
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "").strip()
    if run_id:
        return f"gh{run_id}_{attempt or '1'}"
    return uuid.uuid4().hex[:12]


@pytest.fixture(scope="session")
def base_config() -> DWSConfig:
    return DWSConfig(
        host=_required_env("DWS_HOST"),
        port=int(os.getenv("DWS_PORT") or "8000"),
        username=_required_env("DWS_USERNAME"),
        password=_required_env("DWS_PASSWORD"),
        database=os.getenv("DWS_DATABASE") or "gaussdb",
        schema=os.getenv("DWS_SCHEMA") or "public",
        sslmode=os.getenv("DWS_SSLMODE") or "prefer",
        sslrootcert=os.getenv("DWS_SSLROOTCERT") or None,
    )


@pytest.fixture(scope="session")
def connector(base_config: DWSConfig) -> Generator[DWSConnector, None, None]:
    admin = DWSConnector(base_config)
    assert admin.test_connection()

    schema_name = f"datus_ci_dws_{_run_scope()}"
    _assert_success(admin.execute_ddl(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'), "drop stale schema")
    _assert_success(admin.execute_ddl(f'CREATE SCHEMA "{schema_name}"'), "create isolated test schema")

    scoped = DWSConnector(base_config.model_copy(update={"schema_name": schema_name}))
    try:
        assert scoped.test_connection()
        yield scoped
    finally:
        scoped.close()
        # Runs even when a test raises, so a failed run leaves nothing behind.
        _assert_success(admin.execute_ddl(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'), "drop test schema")
        admin.close()


@pytest.fixture(scope="session")
def dws_objects(connector: DWSConnector) -> DWSConnector:
    schema = connector.schema_name
    for table_name, ddl in TABLE_DDL.items():
        _assert_success(connector.execute_ddl(ddl.format(schema=schema)), f"create {table_name}")

    for table_name, rows in TABLE_DATA.items():
        values = ",\n".join(f"({', '.join(_sql_literal(value) for value in row)})" for row in rows)
        _assert_success(
            connector.execute_insert(f'INSERT INTO "{schema}"."{table_name}" VALUES {values}'),
            f"load {table_name}",
        )

    _assert_success(connector.execute_ddl(VIEW_DDL.format(schema=schema)), "create view")
    return connector
