# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_clickhouse import ClickHouseConfig


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with all required fields."""
    config = ClickHouseConfig(username="default_user")

    assert config.model_dump() == {
        "host": "localhost",
        "port": 8123,
        "username": "default_user",
        "password": "",
        "database": None,
        "timeout_seconds": 30,
        "query_timeout_seconds": None,
        "protocol": "http",
        "verify": True,
        "ca_cert": None,
    }


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with custom values."""
    config = ClickHouseConfig(
        host="192.168.1.100",
        port=8123,
        username="admin",
        password="secret123",
        database="mydb",
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 8123
    assert config.username == "admin"
    assert config.password == "secret123"
    assert config.database == "mydb"
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required fields are missing."""
    with pytest.raises(ValidationError) as exc_info:
        ClickHouseConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_port_type():
    """Test that validation fails for invalid port type."""
    with pytest.raises(ValidationError) as exc_info:
        ClickHouseConfig(username="default_user", port="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        ClickHouseConfig(username="default_user", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        ClickHouseConfig(username="default_user", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_with_empty_password():
    """Test config with empty password."""
    config = ClickHouseConfig(username="default_user", password="")

    assert config.password == ""


def test_config_with_none_database():
    """Test config with None as database."""
    config = ClickHouseConfig(username="default_user", database=None)

    assert config.database is None


def test_config_default_host():
    """Test default host value."""
    config = ClickHouseConfig(username="default_user")

    assert config.host == "localhost"


def test_config_default_port():
    """Test default port value."""
    config = ClickHouseConfig(username="default_user")

    assert config.port == 8123


def test_config_default_timeout():
    """Test default timeout value."""
    config = ClickHouseConfig(username="default_user")

    assert config.timeout_seconds == 30


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "host": "localhost",
        "port": 8123,
        "username": "root",
        "password": "pass123",
        "database": "testdb",
    }

    config = ClickHouseConfig(**config_dict)

    assert config.host == "localhost"
    assert config.port == 8123
    assert config.username == "root"
    assert config.password == "pass123"
    assert config.database == "testdb"


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="root",
        password="pass123",
        database="testdb",
    )

    config_dict = config.model_dump()

    assert config_dict["host"] == "localhost"
    assert config_dict["port"] == 8123
    assert config_dict["username"] == "root"
    assert config_dict["password"] == "pass123"
    assert config_dict["database"] == "testdb"
    assert config_dict["timeout_seconds"] == 30


@pytest.mark.acceptance
def test_config_special_characters_in_password():
    """Test config with special characters in password."""
    special_password = "p@ss!w0rd#$%^&*()"
    config = ClickHouseConfig(username="default_user", password=special_password)

    assert config.password == special_password


def test_config_special_characters_in_database():
    """Test config with special characters in database name."""
    special_db = "test-db_123"
    config = ClickHouseConfig(username="default_user", database=special_db)

    assert config.database == special_db


def test_config_unicode_in_username():
    """Test config with unicode characters in username."""
    unicode_user = "用户名"
    config = ClickHouseConfig(username=unicode_user)

    assert config.username == unicode_user


def test_config_negative_port():
    """Test that negative port values are accepted (no validation)."""
    config = ClickHouseConfig(username="default_user", port=-1)
    assert config.port == -1


def test_config_zero_timeout():
    with pytest.raises(ValidationError, match="timeout_seconds"):
        ClickHouseConfig(username="test_user", timeout_seconds=0)


def test_config_large_port_number():
    """Test config with large port number."""
    config = ClickHouseConfig(username="default_user", port=65535)

    assert config.port == 65535


def test_config_port_out_of_range():
    """Test that port out of valid range is accepted (no validation)."""
    config = ClickHouseConfig(username="default_user", port=70000)
    assert config.port == 70000
