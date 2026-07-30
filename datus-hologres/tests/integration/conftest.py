# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import os
import uuid
from typing import Generator

import pytest

from datus_hologres import HologresConfig, HologresConnector, register

register()

TPCH_DDL = {
    "tpch_region": """
        CREATE TABLE "{schema}"."tpch_region" (
            "r_regionkey" INTEGER NOT NULL PRIMARY KEY,
            "r_name" TEXT NOT NULL,
            "r_comment" TEXT
        )
        WITH (orientation = 'column', distribution_key = 'r_regionkey')
    """,
    "tpch_nation": """
        CREATE TABLE "{schema}"."tpch_nation" (
            "n_nationkey" INTEGER NOT NULL PRIMARY KEY,
            "n_name" TEXT NOT NULL,
            "n_regionkey" INTEGER NOT NULL,
            "n_comment" TEXT
        )
        WITH (orientation = 'column', distribution_key = 'n_nationkey')
    """,
    "tpch_supplier": """
        CREATE TABLE "{schema}"."tpch_supplier" (
            "s_suppkey" INTEGER NOT NULL PRIMARY KEY,
            "s_name" TEXT NOT NULL,
            "s_nationkey" INTEGER NOT NULL,
            "s_acctbal" DECIMAL(15, 2) NOT NULL
        )
        WITH (orientation = 'column', distribution_key = 's_suppkey')
    """,
    "tpch_customer": """
        CREATE TABLE "{schema}"."tpch_customer" (
            "c_custkey" INTEGER NOT NULL PRIMARY KEY,
            "c_name" TEXT NOT NULL,
            "c_nationkey" INTEGER NOT NULL,
            "c_acctbal" DECIMAL(15, 2) NOT NULL,
            "c_mktsegment" TEXT NOT NULL
        )
        WITH (orientation = 'column', distribution_key = 'c_custkey')
    """,
    "tpch_orders": """
        CREATE TABLE "{schema}"."tpch_orders" (
            "o_orderkey" INTEGER NOT NULL PRIMARY KEY,
            "o_custkey" INTEGER NOT NULL,
            "o_orderstatus" TEXT NOT NULL,
            "o_totalprice" DECIMAL(15, 2) NOT NULL,
            "o_orderdate" DATE NOT NULL
        )
        WITH (
            orientation = 'column',
            distribution_key = 'o_orderkey',
            event_time_column = 'o_orderdate'
        )
    """,
}

TPCH_DATA = {
    "tpch_region": [
        (0, "AFRICA", "regional comment"),
        (1, "AMERICA", "regional comment"),
        (2, "ASIA", "regional comment"),
        (3, "EUROPE", "regional comment"),
        (4, "MIDDLE EAST", "regional comment"),
    ],
    "tpch_nation": [
        (0, "ALGERIA", 0, "nation comment"),
        (1, "ARGENTINA", 1, "nation comment"),
        (2, "BRAZIL", 1, "nation comment"),
        (6, "FRANCE", 3, "nation comment"),
        (18, "CHINA", 2, "nation comment"),
    ],
    "tpch_supplier": [
        (1, "Supplier#1", 6, 5755.94),
        (2, "Supplier#2", 18, 4032.68),
        (3, "Supplier#3", 2, 4192.40),
    ],
    "tpch_customer": [
        (1, "Customer#1", 18, 711.56, "BUILDING"),
        (2, "Customer#2", 6, 121.65, "AUTOMOBILE"),
        (3, "Customer#3", 2, 7498.12, "BUILDING"),
        (4, "Customer#4", 1, 2866.83, "MACHINERY"),
    ],
    "tpch_orders": [
        (1, 1, "O", 173665.47, "2024-01-02"),
        (2, 1, "F", 46929.18, "2024-01-03"),
        (3, 2, "O", 193846.25, "2024-02-10"),
        (4, 3, "F", 32151.78, "2024-02-11"),
        (5, 3, "O", 144659.20, "2024-03-05"),
        (6, 4, "F", 58749.59, "2024-03-06"),
    ],
}


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for live Hologres tests")
    return value


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"


def _sql_literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


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
    schema = connector.schema_name
    for table_name, ddl in TPCH_DDL.items():
        drop = connector.execute_ddl(f'DROP TABLE IF EXISTS "{schema}"."{table_name}" CASCADE')
        _assert_success(drop, f"drop stale {table_name}")
        create = connector.execute_ddl(ddl.format(schema=schema))
        _assert_success(create, f"create {table_name}")

    for table_name, rows in TPCH_DATA.items():
        values = ",\n".join(f"({', '.join(_sql_literal(value) for value in row)})" for row in rows)
        insert = connector.execute_insert(f'INSERT INTO "{schema}"."{table_name}" VALUES {values}')
        _assert_success(insert, f"load {table_name}")

    return connector
