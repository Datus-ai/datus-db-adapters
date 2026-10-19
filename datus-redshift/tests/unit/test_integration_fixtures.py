# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, call

import pytest

from datus_db_core import ExecuteSQLResult
from datus_redshift import RedshiftConnector
from tests.integration import conftest as integration_fixtures


@pytest.fixture
def tpch_connector(monkeypatch):
    """Replace the live connection boundary with an in-memory test double."""
    for name, value in {
        "REDSHIFT_HOST": "cluster.example.com",
        "REDSHIFT_USERNAME": "testuser",
        "REDSHIFT_PASSWORD": "testpass",
        "REDSHIFT_IAM": "false",
    }.items():
        monkeypatch.setenv(name, value)
    connector = MagicMock(spec=RedshiftConnector)
    connector.test_connection.return_value = {"success": True}
    connector.execute_ddl.return_value = ExecuteSQLResult(success=True)
    connector.execute_insert.return_value = ExecuteSQLResult(success=True)
    monkeypatch.setattr(integration_fixtures, "RedshiftConnector", lambda config: connector)
    return connector


def test_tpch_setup_preserves_load_error_with_drop_and_close_failures(tpch_connector):
    """A broken connection must retain the load error and every cleanup diagnostic."""
    load_error = RuntimeError("load disconnected")
    tpch_connector.execute_insert.side_effect = load_error
    tpch_connector.execute_ddl.side_effect = [
        *[ExecuteSQLResult(success=True) for _ in range(2 * len(integration_fixtures.TPCH_TABLES))],
        ExecuteSQLResult(success=False, error="drop denied"),
        RuntimeError("drop disconnected"),
        *[ExecuteSQLResult(success=True) for _ in range(len(integration_fixtures.TPCH_TABLES) - 2)],
    ]
    tpch_connector.close.side_effect = RuntimeError("close disconnected")

    with pytest.raises(RuntimeError, match="load disconnected") as raised:
        next(integration_fixtures.tpch_setup.__wrapped__())

    assert raised.value is load_error
    assert raised.value.__notes__ == [
        "TPC-H cleanup failed: tpch_region: drop denied; tpch_nation: drop disconnected; close: close disconnected"
    ]
    assert tpch_connector.execute_ddl.call_args_list[-len(integration_fixtures.TPCH_TABLES) :] == [
        call(f"DROP TABLE IF EXISTS {integration_fixtures.TPCH_SCHEMA}.{table}")
        for table in integration_fixtures.TPCH_TABLES
    ]
    tpch_connector.close.assert_called_once_with()


def test_tpch_setup_reports_close_failure_after_successful_setup(tpch_connector):
    """A close failure must fail teardown when there is no original setup error."""
    tpch_connector.close.side_effect = RuntimeError("close disconnected")
    setup = integration_fixtures.tpch_setup.__wrapped__()
    assert next(setup) is tpch_connector

    with pytest.raises(RuntimeError, match="TPC-H cleanup failed: close: close disconnected"):
        next(setup)

    tpch_connector.close.assert_called_once_with()
