# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live Redshift fixtures; configured connection failures fail the test run.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| REDSHIFT_HOST | required | Original Redshift endpoint, including when tunneling |
| REDSHIFT_USERNAME | required | Database user for password and IAM authentication |
| REDSHIFT_PASSWORD | required without IAM | Database password |
| REDSHIFT_DATABASE | dev | Database |
| REDSHIFT_PORT | 5439 | Connection port, or the local forwarded port |
| REDSHIFT_SCHEMA | public | Schema for connector tests |
| REDSHIFT_TPCH_SCHEMA | REDSHIFT_SCHEMA | Existing schema for TPC-H tables |
| REDSHIFT_IAM | false | Use IAM credentials when true |
| REDSHIFT_CLUSTER_IDENTIFIER | unset | Provisioned cluster ID for IAM authentication |
| REDSHIFT_REGION | unset | AWS region for IAM authentication |

IAM uses the SDK's default AWS credential provider chain.
"""

import os
from typing import Generator

import pytest

from datus_db_core.testing import contract
from datus_redshift import RedshiftConfig, RedshiftConnector
from datus_redshift.tpch_data import ROW_COUNTS, TPCH_DATA, TPCH_DDL, TPCH_TABLES

__all__ = ["ROW_COUNTS", "TPCH_DATA", "TPCH_DDL", "TPCH_TABLES", "TPCH_SCHEMA"]

TPCH_SCHEMA = os.getenv("REDSHIFT_TPCH_SCHEMA", os.getenv("REDSHIFT_SCHEMA", "public"))


def _required_env(name: str) -> str:
    """Skip live tests only when required configuration is absent."""
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is not set")
    return value


def _build_config(schema: str | None = None) -> RedshiftConfig:
    """Build password or IAM settings from the documented environment variables."""
    iam = os.getenv("REDSHIFT_IAM", "false").lower() == "true"
    return RedshiftConfig(
        host=_required_env("REDSHIFT_HOST"),
        username=_required_env("REDSHIFT_USERNAME"),
        password=None if iam else _required_env("REDSHIFT_PASSWORD"),
        database=os.getenv("REDSHIFT_DATABASE", "dev"),
        port=int(os.getenv("REDSHIFT_PORT", "5439")),
        schema=schema or os.getenv("REDSHIFT_SCHEMA", "public"),
        ssl=True,
        timeout_seconds=30,
        iam=iam,
        cluster_identifier=_required_env("REDSHIFT_CLUSTER_IDENTIFIER") if iam else None,
        region=_required_env("REDSHIFT_REGION") if iam else None,
    )


@pytest.fixture
def config() -> RedshiftConfig:
    """Provide the configured live Redshift connection settings."""
    return _build_config()


@pytest.fixture
def connector(config: RedshiftConfig) -> Generator[RedshiftConnector, None, None]:
    """Verify a live connection and close it after each test."""
    conn = RedshiftConnector(config)
    try:
        assert conn.test_connection()["success"] is True
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def tpch_setup() -> Generator[RedshiftConnector, None, None]:
    """Load the shared dataset, then remove its tables even if a test fails."""
    conn = RedshiftConnector(_build_config(schema=TPCH_SCHEMA))
    failure = None
    try:
        assert conn.test_connection()["success"] is True
        for table in TPCH_TABLES:
            contract.assert_success(conn.execute_ddl(f"DROP TABLE IF EXISTS {TPCH_SCHEMA}.{table}"), f"drop {table}")
        for table, ddl in zip(TPCH_TABLES, TPCH_DDL, strict=True):
            contract.assert_success(conn.execute_ddl(ddl.format(schema=TPCH_SCHEMA)), f"create {table}")
        for table, insert in zip(TPCH_TABLES, TPCH_DATA, strict=True):
            contract.assert_success(conn.execute_insert(insert.format(schema=TPCH_SCHEMA)), f"load {table}")
        yield conn
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup_errors = []
        for table in TPCH_TABLES:
            try:
                result = conn.execute_ddl(f"DROP TABLE IF EXISTS {TPCH_SCHEMA}.{table}")
                if not result.success:
                    cleanup_errors.append(f"{table}: {result.error}")
            except Exception as error:
                cleanup_errors.append(f"{table}: {error}")
        try:
            conn.close()
        except Exception as error:
            cleanup_errors.append(f"close: {error}")
        if cleanup_errors:
            message = "TPC-H cleanup failed: " + "; ".join(cleanup_errors)
            if failure is not None:
                failure.add_note(message)
            else:
                raise RuntimeError(message)
