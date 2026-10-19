# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from datus_clickhouse.testing import config_from_env
from datus_db_core import DatusDbException


@pytest.fixture(autouse=True)
def clean_connection_environment(monkeypatch):
    for key in ("HOST", "PORT", "USER", "PASSWORD", "DATABASE", "PROTOCOL", "VERIFY", "CA_CERT"):
        monkeypatch.delenv(f"CLICKHOUSE_{key}", raising=False)


def test_env_defaults_and_explicit_false(monkeypatch):
    assert config_from_env().port == 8123
    monkeypatch.setenv("CLICKHOUSE_PROTOCOL", "https")
    monkeypatch.setenv("CLICKHOUSE_VERIFY", "false")
    config = config_from_env()
    assert config.port == 8443
    assert config.verify is False


def test_cli_overrides_precede_environment_validation(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PROTOCOL", "invalid")
    monkeypatch.setenv("CLICKHOUSE_PORT", "not-a-port")
    config = config_from_env({"protocol": "https", "port": 19443})
    assert (config.protocol, config.port) == ("https", 19443)


@pytest.mark.parametrize("verify", [True, False])
def test_setup_cli_arguments_reach_driver(monkeypatch, verify):
    import certifi
    from clickhouse_sqlalchemy.drivers.http import connector as dbapi

    script = Path(__file__).parents[2] / "scripts" / "init_tpch_data.py"
    monkeypatch.setenv("CLICKHOUSE_PROTOCOL", "invalid")
    monkeypatch.setenv("CLICKHOUSE_PORT", "not-a-port")
    arguments = [
        str(script),
        "--protocol",
        "https",
        "--port",
        "19443",
        "--host",
        "localhost",
        "--username",
        "cli_user",
        "--password",
        "cli_password",
        "--database",
        "cli_database",
        "--verify" if verify else "--no-verify",
    ]
    if verify:
        arguments.extend(["--ca-cert", certifi.where()])
    monkeypatch.setattr("sys.argv", arguments)
    with patch.object(dbapi, "connect", side_effect=RuntimeError("driver unavailable")) as connect:
        with pytest.raises(DatusDbException, match="Connection test failed"):
            runpy.run_path(str(script), run_name="__main__")
    assert connect.call_args.args == ("https://localhost:19443/", "default", "cli_user", "cli_password")
    assert connect.call_args.kwargs["verify"] == (str(Path(certifi.where()).resolve()) if verify else False)


def test_readiness_preserves_https_and_ca(monkeypatch):
    import certifi

    # The pytest console entry point does not add the workspace root to sys.path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("CLICKHOUSE_PROTOCOL", "https")
    monkeypatch.setenv("CLICKHOUSE_CA_CERT", certifi.where())
    with patch("datus_clickhouse.ClickHouseConnector") as factory:
        factory.return_value.test_connection.return_value = True
        runpy.run_module("ci.integration.readiness.clickhouse", run_name="__main__")
    config = factory.call_args.args[0]
    assert (config.protocol, config.port, config.verify) == ("https", 8443, True)
    assert config.ca_cert == str(Path(certifi.where()).resolve())
    factory.return_value.close.assert_called_once()


@pytest.mark.parametrize("required", [False, True])
def test_fixture_handles_optional_and_required_connection_failure(monkeypatch, required):
    monkeypatch.setenv("CLICKHOUSE_REQUIRED", "1" if required else "0")
    module = runpy.run_path(str(Path(__file__).parents[1] / "integration" / "conftest.py"))
    with patch("datus_clickhouse.ClickHouseConnector.test_connection", return_value=False):
        with pytest.raises(RuntimeError if required else pytest.skip.Exception, match="connection test failed"):
            module["_connect_and_create_database"]()
