# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_spark import SparkConfig

# ==================== Basic Validation Tests ====================


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with all required fields."""
    config = SparkConfig(username="test_user")

    assert config.host == "127.0.0.1"
    assert config.port == 10000
    assert config.username == "test_user"
    assert config.password == ""
    assert config.database is None
    assert config.auth_mechanism == "NONE"
    assert config.timeout_seconds == 30


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with custom values."""
    config = SparkConfig(
        host="192.168.1.100",
        port=10001,
        username="admin",
        password="secret123",
        database="mydb",
        auth_mechanism="CUSTOM",
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 10001
    assert config.username == "admin"
    assert config.password == "secret123"
    assert config.database == "mydb"
    assert config.auth_mechanism == "CUSTOM"
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required fields are missing."""
    with pytest.raises(ValidationError) as exc_info:
        SparkConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_port_type():
    """Test that validation fails for invalid port type."""
    with pytest.raises(ValidationError) as exc_info:
        SparkConfig(username="test_user", port="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        SparkConfig(username="test_user", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        SparkConfig(username="test_user", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "host": "localhost",
        "port": 10000,
        "username": "root",
        "password": "pass123",
        "database": "testdb",
    }

    config = SparkConfig(**config_dict)

    assert config.host == "localhost"
    assert config.port == 10000
    assert config.username == "root"
    assert config.password == "pass123"
    assert config.database == "testdb"


# ==================== Spark-Specific Field Tests ====================


def test_config_default_database():
    """Test default database value."""
    config = SparkConfig(username="test_user")

    assert config.database is None


def test_config_custom_database():
    """Test custom database value."""
    config = SparkConfig(username="test_user", database="analytics")

    assert config.database == "analytics"


def test_config_default_port_10000():
    """Test default port is 10000."""
    config = SparkConfig(username="test_user")

    assert config.port == 10000


@pytest.mark.parametrize("auth_mechanism", ["NONE", "NOSASL", "LDAP", "KERBEROS", "CUSTOM"])
def test_config_supported_auth_mechanisms(auth_mechanism):
    """Test every authentication mechanism accepted by PyHive."""
    config = SparkConfig(username="test_user", auth_mechanism=auth_mechanism)

    assert config.auth_mechanism == auth_mechanism


# ==================== Default Value Tests ====================


def test_config_default_host():
    """Test default host value."""
    config = SparkConfig(username="test_user")

    assert config.host == "127.0.0.1"


def test_config_default_timeout():
    """Test default timeout value."""
    config = SparkConfig(username="test_user")

    assert config.timeout_seconds == 30


def test_config_with_none_database():
    """Test config with None as database."""
    config = SparkConfig(username="test_user", database=None)

    assert config.database is None


def test_config_with_empty_password():
    """Test config with empty password."""
    config = SparkConfig(username="test_user", password="")

    assert config.password == ""


# ==================== Edge Case Tests ====================


@pytest.mark.acceptance
def test_config_special_characters_in_password():
    """Test config with special characters in password."""
    special_password = "p@ss!w0rd#$%^&*()"
    config = SparkConfig(username="test_user", password=special_password)

    assert config.password == special_password


def test_config_large_port_number():
    """Test config with large port number."""
    config = SparkConfig(username="test_user", port=65535)

    assert config.port == 65535


def test_config_zero_timeout():
    """Test that zero timeout is allowed."""
    config = SparkConfig(username="test_user", timeout_seconds=0)

    assert config.timeout_seconds == 0


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = SparkConfig(
        host="localhost",
        port=10000,
        username="root",
        password="pass123",
        database="testdb",
    )

    config_dict = config.model_dump()

    assert config_dict["host"] == "localhost"
    assert config_dict["port"] == 10000
    assert config_dict["username"] == "root"
    assert config_dict["password"] == "pass123"
    assert config_dict["database"] == "testdb"
    assert config_dict["auth_mechanism"] == "NONE"
    assert config_dict["timeout_seconds"] == 30


def test_config_database_with_special_characters():
    """Test database with special characters."""
    special_db = "test-db_123"
    config = SparkConfig(username="test_user", database=special_db)

    assert config.database == special_db


@pytest.mark.parametrize("auth_mechanism", ["PLAIN", "INVALID"])
def test_config_invalid_auth_mechanism(auth_mechanism):
    """Test that validation rejects mechanisms unsupported by PyHive."""
    with pytest.raises(ValidationError) as exc_info:
        SparkConfig(username="test_user", auth_mechanism=auth_mechanism)

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("auth_mechanism",) for error in errors)
