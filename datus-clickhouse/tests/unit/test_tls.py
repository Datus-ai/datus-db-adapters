# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""TLS configuration reaches the published driver's real DBAPI boundary."""

import shutil
from pathlib import Path
from unittest.mock import patch

import certifi
import pytest
from pydantic import ValidationError

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector


@pytest.fixture
def ca_file(tmp_path):
    path = tmp_path / "trusted-ca.pem"
    shutil.copyfile(certifi.where(), path)
    return path


@pytest.mark.parametrize(
    "protocol,port,expected", [("http", None, 8123), ("https", None, 8443), ("https", 18443, 18443)]
)
def test_protocol_ports(protocol, port, expected):
    assert ClickHouseConfig(username="u", protocol=protocol, port=port).port == expected


@pytest.mark.parametrize(
    "options,message",
    [
        ({"protocol": "native"}, "protocol"),
        ({"ca_cert": "/missing.pem"}, "ca_cert requires"),
        ({"protocol": "https", "verify": False, "ca_cert": "/missing.pem"}, "ca_cert requires"),
        ({"protocol": "https", "ca_cert": ""}, "readable PEM"),
        ({"protocol": "https", "ca_cert": "/nonexistent-datus-ca.pem"}, "readable PEM"),
    ],
)
def test_tls_config_rejects_unusable_settings_without_password(options, message):
    with pytest.raises(ValidationError, match=message) as exc:
        ClickHouseConfig(username="u", password="do-not-log-this-password", **options)
    assert "do-not-log-this-password" not in str(exc.value)


@pytest.mark.parametrize("kind", ["directory", "invalid-pem"])
def test_ca_must_be_a_certificate_file(tmp_path, kind):
    path = tmp_path
    if kind == "invalid-pem":
        path = tmp_path / "invalid.pem"
        path.write_text("not a certificate")
    with pytest.raises(ValidationError, match="readable PEM"):
        ClickHouseConfig(username="u", protocol="https", ca_cert=str(path))


def test_ca_relative_paths_are_resolved(ca_file, monkeypatch):
    monkeypatch.chdir(ca_file.parent)
    config = ClickHouseConfig(username="u", protocol="https", ca_cert="trusted-ca.pem")
    assert config.ca_cert == str(ca_file.resolve())
    assert "password=" not in repr(config)


@pytest.mark.parametrize(
    "protocol,verify,use_ca",
    [("http", True, False), ("https", True, False), ("https", False, False), ("https", True, True)],
)
@pytest.mark.parametrize("password", ["", "p@ss:/?# 中文"])
@pytest.mark.parametrize("database", ["analytics", " padded database "])
def test_final_dbapi_arguments(ca_file, protocol, verify, use_ca, password, database):
    """Keep URL/dialect/hooks real; intercept only the driver's connect call."""
    config = ClickHouseConfig(
        host="localhost",
        port=18443,
        username="u@中文",
        password=password,
        database=database,
        protocol=protocol,
        verify=verify,
        ca_cert=str(ca_file) if use_ca else None,
    )
    connector = ClickHouseConnector(config)
    engine = connector._ensure_engine()

    class StopBeforeNetwork(Exception):
        pass

    try:
        with patch.object(engine.dialect.loaded_dbapi, "connect", side_effect=StopBeforeNetwork) as connect:
            with pytest.raises(StopBeforeNetwork):
                engine.connect()
        assert connect.call_args.args == (f"{protocol}://localhost:18443/", database, config.username, password)
        assert connect.call_args.kwargs["verify"] == (str(ca_file) if use_ca else verify)
        assert "timeout" not in connect.call_args.kwargs  # TLS does not change query timeout semantics.
        assert Path(ca_file).name not in connector.connection_string
    finally:
        connector.close()
