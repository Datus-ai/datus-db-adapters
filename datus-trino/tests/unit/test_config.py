# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_trino import TrinoConfig

# ==================== Basic Validation Tests ====================


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with all required fields."""
    config = TrinoConfig(username="test_user")

    assert config.model_dump() == {
        "host": "127.0.0.1",
        "port": 8080,
        "username": "test_user",
        "password": "",
        "catalog": "hive",
        "schema_name": "default",
        "http_scheme": "http",
        "verify": True,
        "ca_cert": None,
        "timeout_seconds": 30,
    }


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with custom values."""
    config = TrinoConfig(
        host="192.168.1.100",
        port=443,
        username="admin",
        password="secret123",
        catalog="iceberg",
        schema_name="analytics",
        http_scheme="https",
        verify=False,
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 443
    assert config.username == "admin"
    assert config.password == "secret123"
    assert config.catalog == "iceberg"
    assert config.schema_name == "analytics"
    assert config.http_scheme == "https"
    assert config.verify is False
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required fields are missing."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_port_type():
    """Test that validation fails for invalid port type."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig(username="test_user", port="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig(username="test_user", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


def test_config_invalid_verify_type():
    """Test that validation fails for invalid verify type."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig(username="test_user", verify="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("verify",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig(username="test_user", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "host": "localhost",
        "port": 8080,
        "username": "root",
        "password": "pass123",
        "catalog": "hive",
        "schema_name": "myschema",
    }

    config = TrinoConfig(**config_dict)

    assert config.host == "localhost"
    assert config.port == 8080
    assert config.username == "root"
    assert config.password == "pass123"
    assert config.catalog == "hive"
    assert config.schema_name == "myschema"


# ==================== Trino-Specific Field Tests ====================


def test_config_default_catalog():
    """Test default catalog value."""
    config = TrinoConfig(username="test_user")

    assert config.catalog == "hive"


def test_config_custom_catalog():
    """Test custom catalog value."""
    config = TrinoConfig(username="test_user", catalog="iceberg")

    assert config.catalog == "iceberg"


def test_config_default_schema_name():
    """Test default schema_name value."""
    config = TrinoConfig(username="test_user")

    assert config.schema_name == "default"


def test_config_default_port_8080():
    """Test default port is 8080."""
    config = TrinoConfig(username="test_user")

    assert config.port == 8080


def test_config_https_scheme():
    """Test https scheme."""
    config = TrinoConfig(username="test_user", http_scheme="https", port=443)

    assert config.http_scheme == "https"
    assert config.port == 443


# ==================== Default Value Tests ====================


def test_config_default_host():
    """Test default host value."""
    config = TrinoConfig(username="test_user")

    assert config.host == "127.0.0.1"


def test_config_default_http_scheme():
    """Test default http_scheme value."""
    config = TrinoConfig(username="test_user")

    assert config.http_scheme == "http"


def test_config_default_verify():
    """Test default verify value."""
    config = TrinoConfig(username="test_user")

    assert config.verify is True


def test_config_default_timeout():
    """Test default timeout value."""
    config = TrinoConfig(username="test_user")

    assert config.timeout_seconds == 30


def test_config_with_empty_password():
    """Test config with empty password."""
    config = TrinoConfig(username="test_user", password="")

    assert config.password == ""


# ==================== Edge Case Tests ====================


@pytest.mark.acceptance
def test_config_special_characters_in_password():
    """Test config with special characters in password."""
    special_password = "p@ss!w0rd#$%^&*()"
    config = TrinoConfig(username="test_user", password=special_password)

    assert config.password == special_password


def test_config_large_port_number():
    """Test config with large port number."""
    config = TrinoConfig(username="test_user", port=65535)

    assert config.port == 65535


def test_config_zero_timeout():
    """Test that zero timeout is rejected (gt=0 constraint)."""
    with pytest.raises(ValidationError):
        TrinoConfig(username="test_user", timeout_seconds=0)


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = TrinoConfig(
        host="localhost",
        port=8080,
        username="root",
        password="pass123",
        catalog="hive",
        schema_name="myschema",
    )

    config_dict = config.model_dump()

    assert config_dict["host"] == "localhost"
    assert config_dict["port"] == 8080
    assert config_dict["username"] == "root"
    assert config_dict["password"] == "pass123"
    assert config_dict["catalog"] == "hive"
    assert config_dict["schema_name"] == "myschema"
    assert config_dict["http_scheme"] == "http"
    assert config_dict["verify"] is True
    assert config_dict["timeout_seconds"] == 30


def test_config_catalog_with_special_characters():
    """Test catalog with special characters."""
    special_catalog = "test-catalog_123"
    config = TrinoConfig(username="test_user", catalog=special_catalog)

    assert config.catalog == special_catalog


def test_config_invalid_http_scheme():
    """Test that validation fails for invalid http_scheme."""
    with pytest.raises(ValidationError) as exc_info:
        TrinoConfig(username="test_user", http_scheme="ftp")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("http_scheme",) for error in errors)
