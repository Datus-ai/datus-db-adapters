# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import pytest
from pydantic import ValidationError

from datus_dws import DWSConfig
from datus_dws.config import normalize_dws_endpoint


def _config(**overrides):
    values = {
        "host": "example.dws.myhuaweicloud.com",
        "username": "dbadmin",
        "password": "secret-value",
        "database": "gaussdb",
    }
    values.update(overrides)
    return DWSConfig(**values)


def test_config_defaults():
    config = _config()

    assert config.port == 8000
    assert config.schema_name == "public"
    assert config.sslmode == "prefer"
    assert config.sslrootcert is None
    assert config.timeout_seconds == 30


def test_password_is_not_exposed_in_repr():
    config = _config()

    assert "secret-value" not in repr(config)


def test_schema_alias_is_accepted():
    config = _config(schema="reporting")

    assert config.schema_name == "reporting"


def test_console_endpoint_with_embedded_port():
    config = _config(host="example.dws.myhuaweicloud.com:8123")

    assert config.host == "example.dws.myhuaweicloud.com"
    assert config.port == 8123


def test_embedded_port_matching_explicit_port_is_accepted():
    config = _config(host="example.dws.myhuaweicloud.com:8000", port=8000)

    assert config.port == 8000


def test_embedded_port_conflicting_with_explicit_port_is_rejected():
    with pytest.raises(ValidationError, match="conflicts with explicit port"):
        _config(host="example.dws.myhuaweicloud.com:8123", port=8000)


def test_endpoint_with_scheme_is_rejected():
    with pytest.raises(ValidationError, match="must not include a URI scheme"):
        _config(host="dws://example.dws.myhuaweicloud.com")


def test_endpoint_with_user_information_is_rejected():
    with pytest.raises(ValidationError, match="must not contain user information"):
        _config(host="user:pass@example.dws.myhuaweicloud.com")


def test_endpoint_with_path_is_rejected():
    with pytest.raises(ValidationError, match="only a hostname and optional port"):
        _config(host="example.dws.myhuaweicloud.com/gaussdb")


def test_ipv6_endpoint_is_preserved():
    config = _config(host="[2001:db8::1]:8000")

    assert config.host == "2001:db8::1"
    assert config.port == 8000


def test_out_of_range_port_is_rejected():
    with pytest.raises(ValidationError):
        _config(host="example.dws.myhuaweicloud.com:70000")


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
def test_supported_sslmodes_are_accepted(mode):
    assert _config(sslmode=mode).sslmode == mode


def test_unknown_sslmode_is_rejected():
    with pytest.raises(ValidationError):
        _config(sslmode="verify-everything")


def test_sslrootcert_accepts_path_and_inline_pem():
    assert _config(sslrootcert="/etc/ssl/cacert.pem").sslrootcert == "/etc/ssl/cacert.pem"

    pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
    assert _config(sslrootcert=pem).sslrootcert == pem


def test_database_is_required():
    with pytest.raises(ValidationError):
        DWSConfig(host="example.dws.myhuaweicloud.com", username="dbadmin", password="x")


def test_username_is_required():
    with pytest.raises(ValidationError):
        DWSConfig(host="example.dws.myhuaweicloud.com", database="gaussdb", password="x")


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        _config(driver="pg8000")


def test_normalize_endpoint_defaults_to_dws_port():
    assert normalize_dws_endpoint("example.dws.myhuaweicloud.com") == ("example.dws.myhuaweicloud.com", 8000)


def test_normalize_endpoint_rejects_empty_host():
    with pytest.raises(ValueError, match="host is required"):
        normalize_dws_endpoint("")


def test_normalize_endpoint_rejects_non_numeric_port():
    with pytest.raises(ValueError, match="Invalid DWS port"):
        normalize_dws_endpoint("example.dws.myhuaweicloud.com", "eight-thousand")
