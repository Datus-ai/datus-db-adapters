# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_doris import DorisConfig

# ==================== Basic Validation Tests ====================


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with all required fields."""
    config = DorisConfig(username="test_user")

    assert config.host == "127.0.0.1"
    assert config.port == 9030
    assert config.username == "test_user"
    assert config.password == ""
    assert config.catalog == "internal"
    assert config.database is None
    assert config.charset == "utf8mb4"
    assert config.autocommit is True
    assert config.timeout_seconds == 30


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with custom values."""
    config = DorisConfig(
        host="192.168.1.100",
        port=9031,
        username="admin",
        password="secret123",
        catalog="my_catalog",
        database="mydb",
        charset="utf8",
        autocommit=False,
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 9031
    assert config.username == "admin"
    assert config.password == "secret123"
    assert config.catalog == "my_catalog"
    assert config.database == "mydb"
    assert config.charset == "utf8"
    assert config.autocommit is False
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required fields are missing."""
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_port_type():
    """Test that validation fails for invalid port type."""
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", port="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


def test_config_invalid_autocommit_type():
    """Test that validation fails for invalid autocommit type."""
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", autocommit="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("autocommit",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "host": "localhost",
        "port": 9030,
        "username": "root",
        "password": "pass123",
        "catalog": "test_catalog",
        "database": "testdb",
    }

    config = DorisConfig(**config_dict)

    assert config.host == "localhost"
    assert config.port == 9030
    assert config.username == "root"
    assert config.password == "pass123"
    assert config.catalog == "test_catalog"
    assert config.database == "testdb"


# ==================== Doris-Specific Field Tests ====================


def test_config_default_catalog():
    """Test default catalog value."""
    config = DorisConfig(username="test_user")

    assert config.catalog == "internal"


def test_config_custom_catalog():
    """Test custom catalog value."""
    config = DorisConfig(username="test_user", catalog="custom_catalog")

    assert config.catalog == "custom_catalog"


def test_config_empty_catalog():
    """Test empty catalog string."""
    config = DorisConfig(username="test_user", catalog="")

    assert config.catalog == ""


def test_config_default_port_9030():
    """Test default port is 9030 (not 3306 like MySQL)."""
    config = DorisConfig(username="test_user")

    assert config.port == 9030


def test_config_catalog_with_special_characters():
    """Test catalog with special characters."""
    special_catalog = "test-catalog_123"
    config = DorisConfig(username="test_user", catalog=special_catalog)

    assert config.catalog == special_catalog


# ==================== Default Value Tests ====================


def test_config_default_host():
    """Test default host value."""
    config = DorisConfig(username="test_user")

    assert config.host == "127.0.0.1"


def test_config_default_charset():
    """Test default charset value."""
    config = DorisConfig(username="test_user")

    assert config.charset == "utf8mb4"


def test_config_default_autocommit():
    """Test default autocommit value."""
    config = DorisConfig(username="test_user")

    assert config.autocommit is True


def test_config_default_timeout():
    """Test default timeout value."""
    config = DorisConfig(username="test_user")

    assert config.timeout_seconds == 30


def test_config_with_none_database():
    """Test config with None as database."""
    config = DorisConfig(username="test_user", database=None)

    assert config.database is None


def test_config_with_empty_password():
    """Test config with empty password."""
    config = DorisConfig(username="test_user", password="")

    assert config.password == ""


# ==================== Edge Case Tests ====================


@pytest.mark.acceptance
def test_config_special_characters_in_password():
    """Test config with special characters in password."""
    special_password = "p@ss!w0rd#$%^&*()"
    config = DorisConfig(username="test_user", password=special_password)

    assert config.password == special_password


def test_config_unicode_in_catalog():
    """Test config with unicode characters in catalog name."""
    unicode_catalog = "目录名"
    config = DorisConfig(username="test_user", catalog=unicode_catalog)

    assert config.catalog == unicode_catalog


def test_config_large_port_number():
    """Test config with large port number."""
    config = DorisConfig(username="test_user", port=65535)

    assert config.port == 65535


def test_config_port_out_of_range():
    """Test that port out of valid range is accepted (no validation)."""
    config = DorisConfig(username="test_user", port=70000)
    assert config.port == 70000


def test_config_zero_timeout():
    """Test that zero timeout is allowed."""
    config = DorisConfig(username="test_user", timeout_seconds=0)

    assert config.timeout_seconds == 0


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = DorisConfig(
        host="localhost",
        port=9030,
        username="root",
        password="pass123",
        catalog="test_catalog",
        database="testdb",
    )

    config_dict = config.model_dump()

    assert config_dict["host"] == "localhost"
    assert config_dict["port"] == 9030
    assert config_dict["username"] == "root"
    assert config_dict["password"] == "pass123"
    assert config_dict["catalog"] == "test_catalog"
    assert config_dict["database"] == "testdb"
    assert config_dict["charset"] == "utf8mb4"
    assert config_dict["autocommit"] is True
    assert config_dict["timeout_seconds"] == 30
