# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os

import pytest

from datus_db_core import DatusDbException
from datus_gaussdb import GaussDBConfig, GaussDBConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: GaussDBConfig):
    """Connect through the platform-selected driver using a config object."""
    conn = GaussDBConnector(config)
    try:
        assert conn.test_connection()
        assert conn.dialect == "gaussdb"
        sqlalchemy_driver = "psycopg2" if config.driver == "psycopg2" else "psycopg"
        assert conn.connection_string.startswith(f"gaussdb+{sqlalchemy_driver}://")
    finally:
        conn.close()


@pytest.mark.integration
def test_connection_with_dict():
    """Connect using a plain dict config (the datasource YAML shape)."""
    conn = GaussDBConnector(
        {
            "host": os.getenv("GAUSSDB_HOST", "127.0.0.1"),
            "port": int(os.getenv("GAUSSDB_PORT", "25434")),
            "username": os.getenv("GAUSSDB_USER", "datus"),
            "password": os.getenv("GAUSSDB_PASSWORD", "Datus@123"),
            "database": os.getenv("GAUSSDB_DATABASE", "postgres"),
        }
    )
    try:
        assert conn.test_connection()
    finally:
        conn.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select_literal(connector: GaussDBConnector):
    """The simplest possible round-trip through execute_query."""
    result = connector.execute_query("SELECT 1 AS num", result_format="list")

    assert result.success, result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
@pytest.mark.acceptance
def test_server_is_gaussdb_family(connector: GaussDBConnector):
    """version() identifies an openGauss / GaussDB server, not vanilla PostgreSQL."""
    result = connector.execute_query("SELECT version() AS v", result_format="list")

    assert result.success, result.error
    version = result.sql_return[0]["v"]
    assert "openGauss" in version or "GaussDB" in version, version


@pytest.mark.integration
def test_server_version_info_is_parsed(connector: GaussDBConnector):
    """The dialect turns GaussDB's PostgreSQL compatibility level into a tuple."""
    connector.connect()
    engine = connector._get_engine()
    with engine.connect() as conn:
        version_info = engine.dialect._get_server_version_info(conn)

    assert isinstance(version_info, tuple)
    assert version_info[0] >= 9


@pytest.mark.integration
def test_connection_failure_raises_datus_exception(config: GaussDBConfig):
    """An unreachable endpoint surfaces as a typed Datus error, not a driver error."""
    unreachable = config.model_copy(update={"port": 1})
    conn = GaussDBConnector(unreachable)
    try:
        with pytest.raises(DatusDbException):
            conn.test_connection()
    finally:
        conn.close()
