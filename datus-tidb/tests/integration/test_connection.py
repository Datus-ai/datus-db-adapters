# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_db_core import DatusDbException
from datus_tidb import TiDBConfig, TiDBConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection(connector: TiDBConnector):
    assert connector.test_connection() is True


@pytest.mark.integration
def test_server_identifies_as_tidb(connector: TiDBConnector):
    """TiDB reports a MySQL-shaped version string with its own suffix, e.g.
    `8.0.11-TiDB-v8.5.0` — the marker that this is not plain MySQL."""
    result = connector.execute({"sql_query": "SELECT VERSION() AS version"}, result_format="list")

    assert result.success, result.error
    assert "tidb" in result.sql_return[0]["version"].lower()


@pytest.mark.integration
def test_connector_reports_its_own_dialect(connector: TiDBConnector):
    assert connector.dialect == "tidb"
    assert connector.get_type() == "tidb"


@pytest.mark.integration
def test_context_manager_closes_cleanly(config: TiDBConfig):
    conn = TiDBConnector(config)
    try:
        assert conn.test_connection() is True
    finally:
        conn.close()

    # A closed connector reconnects on demand rather than staying broken.
    assert conn.test_connection() is True
    conn.close()


@pytest.mark.integration
def test_bad_credentials_fail_clearly(config: TiDBConfig):
    bad = config.model_copy(update={"username": "no_such_user", "password": "wrong"})
    conn = TiDBConnector(bad)
    try:
        with pytest.raises(DatusDbException):
            conn.test_connection()
    finally:
        conn.close()
