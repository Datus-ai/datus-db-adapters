# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""TLS configuration reaches the published driver's real DBAPI boundary."""

import shutil
from pathlib import Path
from unittest.mock import patch

import certifi
import pytest
from pydantic import ValidationError

from datus_trino import TrinoConfig, TrinoConnector


@pytest.fixture
def ca_file(tmp_path):
    path = tmp_path / "trusted-ca.pem"
    shutil.copyfile(certifi.where(), path)
    return path


@pytest.mark.parametrize(
    "options,message",
    [
        ({"http_scheme": "native"}, "http_scheme"),
        ({"ca_cert": "/missing.pem"}, "ca_cert requires"),
        ({"http_scheme": "https", "verify": False, "ca_cert": "/missing.pem"}, "ca_cert requires"),
        ({"http_scheme": "https", "ca_cert": ""}, "readable PEM"),
        ({"http_scheme": "https", "ca_cert": "/nonexistent-datus-ca.pem"}, "readable PEM"),
    ],
)
def test_tls_config_rejects_unusable_settings_without_password(options, message):
    with pytest.raises(ValidationError, match=message) as exc:
        TrinoConfig(username="u", password="do-not-log-this-password", **options)
    assert "do-not-log-this-password" not in str(exc.value)


@pytest.mark.parametrize("kind", ["directory", "invalid-pem"])
def test_ca_must_be_a_certificate_file(tmp_path, kind):
    path = tmp_path
    if kind == "invalid-pem":
        path = tmp_path / "invalid.pem"
        path.write_text("not a certificate")
    with pytest.raises(ValidationError, match="readable PEM"):
        TrinoConfig(username="u", http_scheme="https", ca_cert=str(path))


def test_ca_relative_paths_are_resolved(ca_file, monkeypatch):
    monkeypatch.chdir(ca_file.parent)
    config = TrinoConfig(username="u", http_scheme="https", ca_cert="trusted-ca.pem")
    assert config.ca_cert == str(ca_file.resolve())
    assert "password=" not in repr(config)


@pytest.mark.parametrize(
    "http_scheme,verify,use_ca",
    [("http", True, False), ("https", True, False), ("https", False, False), ("https", True, True)],
)
@pytest.mark.parametrize("password", ["", "p+%2F@ss:/?# 中文"])
@pytest.mark.parametrize("catalog,schema", [("hive", "analytics"), ("padded + catalog", "schema/+中文")])
def test_final_dbapi_arguments(ca_file, http_scheme, verify, use_ca, password, catalog, schema):
    """Keep URL/dialect/hooks real; intercept only the driver's connect call."""
    config = TrinoConfig(
        host="localhost",
        port=18443,
        username="u+%2F@中文",
        password=password,
        catalog=catalog,
        schema_name=schema,
        http_scheme=http_scheme,
        verify=verify,
        ca_cert=str(ca_file) if use_ca else None,
    )
    connector = TrinoConnector(config)
    engine = connector._ensure_engine()

    class StopBeforeNetwork(Exception):
        pass

    try:
        with patch.object(engine.dialect.loaded_dbapi, "connect", side_effect=StopBeforeNetwork) as connect:
            with pytest.raises(StopBeforeNetwork):
                engine.connect()
        assert connect.call_args.args == ()
        args = connect.call_args.kwargs
        assert args["host"] == "localhost"
        assert args["port"] == 18443
        assert args["http_scheme"] == http_scheme
        assert args["catalog"] == catalog
        assert args["schema"] == schema
        assert args["user"] == config.username
        if password:
            from trino.auth import BasicAuthentication

            assert args["auth"] == BasicAuthentication(config.username, password)
        else:
            assert "auth" not in args
        assert connect.call_args.kwargs["verify"] == (str(ca_file) if use_ca else verify)
        assert "timeout" not in connect.call_args.kwargs  # TLS does not change query timeout semantics.
        assert Path(ca_file).name not in connector.connection_string
    finally:
        connector.close()


def test_environment_and_readiness_retain_tls(monkeypatch, ca_file):
    from datus_trino.testing import config_from_env

    for key, value in {
        "TRINO_HTTP_SCHEME": "https",
        "TRINO_PORT": "18443",
        "TRINO_VERIFY": "true",
        "TRINO_CA_CERT": str(ca_file),
    }.items():
        monkeypatch.setenv(key, value)
    config = config_from_env()
    assert config.http_scheme == "https"
    assert config.port == 18443
    assert config.verify is True
    assert config.ca_cert == str(ca_file)

    import runpy
    import sys

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    sys.modules.pop("ci.integration.readiness.trino", None)
    with patch("ci.integration.readiness._common.require_connection") as ready:
        runpy.run_module("ci.integration.readiness.trino", run_name="__main__")
    conn = ready.call_args.args[1]
    assert ready.call_args.args[0] == "trino"
    assert conn.trino_config.ca_cert == str(ca_file)
    assert conn.trino_config.verify is True
    assert conn.trino_config.http_scheme == "https"
    conn.close()
