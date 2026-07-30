# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import pytest
from pydantic import ValidationError

from datus_hologres import HologresConfig


def test_config_defaults():
    config = HologresConfig(
        host="example.hologres.aliyuncs.com",
        username="access-id",
        password="access-secret",
        database="analytics",
    )

    assert config.port == 80
    assert config.schema_name == "public"
    assert config.sslmode == "prefer"
    assert config.timeout_seconds == 30


def test_config_accepts_access_key_aliases():
    config = HologresConfig(
        host="example.hologres.aliyuncs.com",
        access_key_id="access-id",
        access_key_secret="access-secret",
        database="analytics",
        schema="reporting",
        sslmode="disable",
    )

    assert config.username == "access-id"
    assert config.password == "access-secret"
    assert config.schema_name == "reporting"
    assert "access-secret" not in repr(config)


def test_config_accepts_console_endpoint_with_embedded_port():
    config = HologresConfig(
        host="example.hologres.aliyuncs.com:81",
        username="access-id",
        password="access-secret",
        database="analytics",
    )

    assert config.host == "example.hologres.aliyuncs.com"
    assert config.port == 81


def test_config_rejects_conflicting_embedded_and_explicit_ports():
    with pytest.raises(ValidationError, match="conflicts"):
        HologresConfig(
            host="example.hologres.aliyuncs.com:81",
            port=80,
            username="access-id",
            password="access-secret",
            database="analytics",
        )


@pytest.mark.parametrize("port", [0, 65536])
def test_config_rejects_out_of_range_port(port):
    with pytest.raises(ValidationError, match="between 1 and 65535"):
        HologresConfig(
            host="example.hologres.aliyuncs.com",
            port=port,
            username="access-id",
            password="access-secret",
            database="analytics",
        )


@pytest.mark.parametrize("field", ["host", "username", "password", "database"])
def test_config_requires_connection_fields(field):
    values = {
        "host": "example.hologres.aliyuncs.com",
        "username": "access-id",
        "password": "access-secret",
        "database": "analytics",
    }
    values.pop(field)

    with pytest.raises(ValidationError):
        HologresConfig(**values)


@pytest.mark.parametrize("sslmode", ["invalid", "on", "true"])
def test_config_rejects_unknown_sslmode(sslmode):
    with pytest.raises(ValidationError):
        HologresConfig(
            host="example.hologres.aliyuncs.com",
            username="access-id",
            password="access-secret",
            database="analytics",
            sslmode=sslmode,
        )


def test_config_rejects_invalid_port_and_timeout():
    with pytest.raises(ValidationError):
        HologresConfig(
            host="example.hologres.aliyuncs.com",
            username="access-id",
            password="access-secret",
            database="analytics",
            port=0,
            timeout_seconds=0,
        )
