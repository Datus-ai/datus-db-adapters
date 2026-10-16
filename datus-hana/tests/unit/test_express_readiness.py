"""Bootstrap provisioning must fail visibly after a successful wire connection."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from datus_hana import HanaConfig

SCRIPT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "wait_for_hana.py"))
MAIN = SCRIPT["main"]


def setup_bootstrap(monkeypatch, username="DATUS_TEST"):
    config = HanaConfig(host="localhost", username=username, password="BootstrapOnly9", schema="DATUS_TEST")
    monkeypatch.setitem(MAIN.__globals__, "config_from_env", lambda: config)
    monkeypatch.setenv("DATUS_TEST_HANA_PASSWORD", "BootstrapOnly9")
    connection = Mock()
    connection.cursor.return_value.fetchone.side_effect = [(1,), None, ("DATUS_TEST",)]
    connect = Mock(return_value=connection)
    monkeypatch.setattr(MAIN.__globals__["dbapi"], "connect", connect)
    monkeypatch.setattr(MAIN.__globals__["HanaConnector"], "test_connection", lambda self: True)
    return connection, connect


def test_bootstrap_provisions_the_safe_ci_identity_and_closes(monkeypatch):
    connection, connect = setup_bootstrap(monkeypatch)
    MAIN()
    statements = [call.args[0] for call in connection.cursor.return_value.execute.call_args_list]
    assert 'CREATE USER DATUS_TEST PASSWORD "BootstrapOnly9" NO FORCE_FIRST_PASSWORD_CHANGE' in statements
    assert not any(statement.startswith("CREATE SCHEMA") for statement in statements)
    connection.commit.assert_called_once_with()
    connection.cursor.return_value.close.assert_called_once_with()
    connection.close.assert_called_once_with()
    assert connect.call_args.kwargs["encrypt"] is False


def test_provisioning_failure_after_connection_is_not_retried_or_skipped(monkeypatch):
    connection, connect = setup_bootstrap(monkeypatch)
    connection.cursor.return_value.execute.side_effect = RuntimeError("provisioning failed")
    with pytest.raises(RuntimeError, match="provisioning failed"):
        MAIN()
    connect.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once_with()


@pytest.mark.parametrize("username", ["lowercase", "Mixed User", "CI;DROP", 'CI"USER'])
def test_bootstrap_rejects_names_outside_its_ci_contract_before_connection(monkeypatch, username):
    _, connect = setup_bootstrap(monkeypatch, username)
    with pytest.raises(ValueError, match="uppercase ASCII"):
        MAIN()
    connect.assert_not_called()
