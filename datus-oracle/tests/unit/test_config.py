# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_oracle import OracleConfig


@pytest.mark.acceptance
def test_config_with_service_name():
    config = OracleConfig(username="datus_test", service_name="FREEPDB1")

    assert config.host == "127.0.0.1"
    assert config.port == 1521
    assert config.username == "datus_test"
    assert config.password == ""
    assert config.service_name == "FREEPDB1"
    assert config.sid is None
    assert config.dsn is None
    assert config.schema_name is None
    assert config.timeout_seconds == 30


@pytest.mark.acceptance
def test_config_with_custom_values():
    config = OracleConfig(
        host="192.168.1.100",
        port=1522,
        username="admin",
        password="secret123",
        service_name="ORCLPDB1",
        schema_name="SALES",
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 1522
    assert config.password == "secret123"
    assert config.service_name == "ORCLPDB1"
    assert config.schema_name == "SALES"
    assert config.timeout_seconds == 60


def test_config_schema_alias():
    config = OracleConfig(username="u", service_name="S", schema="MY_SCHEMA")
    assert config.schema_name == "MY_SCHEMA"


def test_config_database_alias_maps_to_service_name():
    config = OracleConfig(username="u", database="FREEPDB1")
    assert config.service_name == "FREEPDB1"


def test_config_database_alias_does_not_override_service_name():
    config = OracleConfig(username="u", database="IGNORED", service_name="FREEPDB1")
    assert config.service_name == "FREEPDB1"


def test_config_with_sid():
    config = OracleConfig(username="u", sid="XE")
    assert config.sid == "XE"
    assert config.service_name is None


def test_config_with_dsn():
    config = OracleConfig(username="u", dsn="prod_tns_alias")
    assert config.dsn == "prod_tns_alias"


@pytest.mark.acceptance
def test_config_missing_connection_target():
    with pytest.raises(ValidationError, match="One of service_name, sid or dsn is required"):
        OracleConfig(username="u")


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "kwargs",
    [
        {"service_name": "S", "sid": "XE"},
        {"service_name": "S", "dsn": "alias"},
        {"sid": "XE", "dsn": "alias"},
        {"service_name": "S", "sid": "XE", "dsn": "alias"},
    ],
)
def test_config_mutually_exclusive_targets(kwargs):
    with pytest.raises(ValidationError, match="mutually exclusive"):
        OracleConfig(username="u", **kwargs)


def test_config_missing_username():
    with pytest.raises(ValidationError) as exc_info:
        OracleConfig(service_name="S")

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OracleConfig(username="u", service_name="S", warehouse="nope")


def test_config_password_field_marked_as_password_input():
    field = OracleConfig.model_fields["password"]
    assert field.json_schema_extra == {"input_type": "password"}
