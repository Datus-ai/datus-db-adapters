# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_gaussdb import GaussDBConfig
from datus_postgresql import PostgreSQLConfig

# ==================== Inherited PostgreSQL Fields ====================


@pytest.mark.acceptance
def test_config_is_a_postgresql_config():
    """GaussDBConfig must stay assignable wherever a PostgreSQLConfig is expected."""
    config = GaussDBConfig(username="datus")
    assert isinstance(config, PostgreSQLConfig)


@pytest.mark.acceptance
def test_config_defaults():
    """Defaults are inherited from PostgreSQLConfig."""
    config = GaussDBConfig(username="datus")

    assert config.host == "127.0.0.1"
    assert config.port == 5432
    assert config.username == "datus"
    assert config.password == ""
    assert config.database is None
    assert config.schema_name == "public"
    assert config.sslmode == "prefer"
    assert config.timeout_seconds == 30


@pytest.mark.acceptance
def test_config_with_custom_values():
    """All inherited connection fields accept custom values."""
    config = GaussDBConfig(
        host="192.168.1.100",
        port=25434,
        username="datus",
        password="Datus@123",
        database="postgres",
        schema_name="myschema",
        sslmode="require",
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 25434
    assert config.username == "datus"
    assert config.password == "Datus@123"
    assert config.database == "postgres"
    assert config.schema_name == "myschema"
    assert config.sslmode == "require"
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_username():
    """username is the only required field."""
    with pytest.raises(ValidationError) as exc_info:
        GaussDBConfig()

    errors = exc_info.value.errors()
    assert [error["loc"] for error in errors] == [("username",)]
    assert errors[0]["type"] == "missing"


@pytest.mark.acceptance
def test_config_invalid_port_type():
    """Non-numeric port is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        GaussDBConfig(username="datus", port="invalid")

    assert any(error["loc"] == ("port",) for error in exc_info.value.errors())


# ==================== schema Alias ====================


@pytest.mark.acceptance
def test_config_schema_alias_populates_schema_name():
    """The YAML-facing 'schema' key populates schema_name."""
    config = GaussDBConfig(username="datus", schema="analytics")
    assert config.schema_name == "analytics"


@pytest.mark.acceptance
def test_config_schema_name_still_accepted():
    """populate_by_name keeps the field name usable alongside the alias."""
    config = GaussDBConfig(username="datus", schema_name="analytics")
    assert config.schema_name == "analytics"


# ==================== driver Field ====================


@pytest.mark.acceptance
@pytest.mark.parametrize(("platform_name", "expected"), [("linux", "gaussdb"), ("darwin", "pg8000")])
def test_config_driver_default_is_platform_safe(monkeypatch, platform_name, expected):
    """Linux keeps the official driver; macOS uses the pure-Python pg8000
    path, which speaks GaussDB SHA256 without the unavailable libpq."""
    monkeypatch.setattr("datus_gaussdb.config.sys.platform", platform_name)
    config = GaussDBConfig(username="datus")
    assert config.driver == expected


@pytest.mark.acceptance
def test_config_driver_psycopg2_accepted():
    """psycopg2 stays available as an escape hatch."""
    config = GaussDBConfig(username="datus", driver="psycopg2")
    assert config.driver == "psycopg2"


@pytest.mark.acceptance
def test_config_driver_pg8000_accepted():
    config = GaussDBConfig(username="datus", driver="pg8000")
    assert config.driver == "pg8000"


@pytest.mark.acceptance
def test_config_sslrootcert_default_none_and_roundtrip():
    assert GaussDBConfig(username="datus").sslrootcert is None
    config = GaussDBConfig(username="datus", sslrootcert="/etc/ssl/gauss-ca.pem")
    assert config.model_dump()["sslrootcert"] == "/etc/ssl/gauss-ca.pem"


@pytest.mark.acceptance
def test_config_driver_invalid_rejected():
    """Any other driver value is rejected by the Literal type."""
    with pytest.raises(ValidationError) as exc_info:
        GaussDBConfig(username="datus", driver="psycopg3")

    assert any(error["loc"] == ("driver",) for error in exc_info.value.errors())


@pytest.mark.acceptance
def test_config_driver_in_model_dump():
    """driver round-trips through model_dump alongside inherited fields."""
    config = GaussDBConfig(username="datus", database="postgres", driver="psycopg2")
    dumped = config.model_dump()

    assert dumped["driver"] == "psycopg2"
    assert dumped["username"] == "datus"
    assert dumped["database"] == "postgres"
    assert dumped["schema_name"] == "public"


# ==================== extra="forbid" ====================


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Unknown keys are rejected rather than silently ignored."""
    with pytest.raises(ValidationError) as exc_info:
        GaussDBConfig(username="datus", extra_field="not_allowed")

    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())


@pytest.mark.acceptance
def test_config_from_dict():
    """Dict construction mirrors the datasource YAML shape."""
    config = GaussDBConfig(
        **{
            "host": "127.0.0.1",
            "port": 25434,
            "username": "datus",
            "password": "Datus@123",
            "database": "postgres",
            "schema": "public",
            "driver": "gaussdb",
        }
    )

    assert config.host == "127.0.0.1"
    assert config.port == 25434
    assert config.schema_name == "public"
    assert config.driver == "gaussdb"
