"""Exercise test configuration and the manual loader at their public entry points."""

import importlib.util
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from datus_hana.testing import config_from_env
from datus_hana.tpch_data import TPCH_DATA, TPCH_DDL

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def clean_hana_environment(monkeypatch):
    for name in os.environ:
        if name.startswith("HANA_"):
            monkeypatch.delenv(name)


def test_cli_overrides_are_validated_after_environment_defaults(monkeypatch):
    monkeypatch.setenv("HANA_HOST", "environment-host")
    monkeypatch.setenv("HANA_PORT", "invalid-environment-port")
    config = config_from_env(host="cli-host", port=443, username="U", password="test-only", schema_name="APP")
    assert (config.host, config.port, config.username, config.schema_name) == ("cli-host", 443, "U", "APP")
    assert config.encrypt is True


def test_environment_userstore_auth_does_not_require_password_endpoint(monkeypatch):
    monkeypatch.setenv("HANA_USERKEY", "TEST_KEY")
    monkeypatch.setenv("HANA_SCHEMA", "APP")
    config = config_from_env()
    assert (config.userkey, config.host, config.username, config.password, config.schema_name) == (
        "TEST_KEY",
        None,
        None,
        "",
        "APP",
    )


def test_manual_tpch_loader_accepts_credentials_only_on_cli(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "init_tpch_data.py",
            "--host",
            "h",
            "--port",
            "443",
            "--username",
            "U",
            "--password",
            "test-only",
            "--schema",
            "APP",
        ],
    )
    with patch("datus_hana.connector.dbapi.connect") as connect:
        cursor = connect.return_value.cursor.return_value

        def execute(sql, parameters=None):
            if sql == "SELECT 1 FROM DUMMY":
                cursor.description = [("VALUE",)]
                cursor.fetchmany.side_effect = [[(1,)], []]
            elif "FROM SYS.TABLES" in sql:
                cursor.description = [("SCHEMA_NAME",), ("OBJECT_NAME",)]
                cursor.fetchmany.side_effect = [[]]
            else:
                cursor.description = None
            cursor.rowcount = 0

        cursor.execute.side_effect = execute
        runpy.run_path(str(PACKAGE_ROOT / "scripts/init_tpch_data.py"), run_name="__main__")

    args = connect.call_args.kwargs
    assert (args["address"], args["port"], args["user"], args["currentSchema"]) == ("h", 443, "U", "APP")
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert statements == [
        "SELECT 1 FROM DUMMY",
        "SELECT SCHEMA_NAME, TABLE_NAME AS OBJECT_NAME FROM SYS.TABLES WHERE SCHEMA_NAME = ? ORDER BY TABLE_NAME",
        *TPCH_DDL,
        *TPCH_DATA,
    ]


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("missing", ["HANA_SCHEMA", "HANA_HOST", "HANA_USER", "HANA_PASSWORD"])
def test_incomplete_password_environment_stops_before_sdk(monkeypatch, required, missing):
    for name, value in {"HANA_SCHEMA": "APP", "HANA_HOST": "h", "HANA_USER": "U", "HANA_PASSWORD": "test-only"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)
    monkeypatch.setenv("HANA_REQUIRE_INTEGRATION", str(int(required)))
    spec = importlib.util.spec_from_file_location(
        "hana_integration_fixtures", PACKAGE_ROOT / "tests/integration/conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with patch("datus_hana.connector.dbapi.connect") as connect:
        with pytest.raises(
            pytest.fail.Exception if required else pytest.skip.Exception, match="incomplete|not configured"
        ):
            module.config.__wrapped__()
    connect.assert_not_called()
