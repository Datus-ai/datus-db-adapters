# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from datus_postgresql import PostgreSQLConfig
from pydantic import ValidationError


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with all required fields."""
    config = PostgreSQLConfig(username="test_user")

    assert config.host == "127.0.0.1"
    assert config.port == 5432
    assert config.username == "test_user"
    assert config.password == ""
    assert config.database is None
    assert config.schema_name == "public"
    assert config.sslmode == "prefer"
    assert config.timeout_seconds == 30


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with custom values."""
    config = PostgreSQLConfig(
        host="192.168.1.100",
        port=5433,
        username="admin",
        password="secret123",
        database="mydb",
        schema_name="myschema",
        sslmode="require",
        timeout_seconds=60,
    )

    assert config.host == "192.168.1.100"
    assert config.port == 5433
    assert config.username == "admin"
    assert config.password == "secret123"
    assert config.database == "mydb"
    assert config.schema_name == "myschema"
    assert config.sslmode == "require"
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required fields are missing."""
    with pytest.raises(ValidationError) as exc_info:
        PostgreSQLConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("username",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_port_type():
    """Test that validation fails for invalid port type."""
    with pytest.raises(ValidationError) as exc_info:
        PostgreSQLConfig(username="test_user", port="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("port",) for error in errors)


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        PostgreSQLConfig(username="test_user", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        PostgreSQLConfig(username="test_user", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_with_empty_password():
    """Test config with empty password."""
    config = PostgreSQLConfig(username="test_user", password="")

    assert config.password == ""


def test_config_with_none_database():
    """Test config with None as database."""
    config = PostgreSQLConfig(username="test_user", database=None)

    assert config.database is None


def test_config_default_host():
    """Test default host value."""
    config = PostgreSQLConfig(username="test_user")

    assert config.host == "127.0.0.1"


def test_config_default_port():
    """Test default port value."""
    config = PostgreSQLConfig(username="test_user")

    assert config.port == 5432


def test_config_default_schema():
    """Test default schema value."""
    config = PostgreSQLConfig(username="test_user")

    assert config.schema_name == "public"


def test_config_default_sslmode():
    """Test default sslmode value."""
    config = PostgreSQLConfig(username="test_user")

    assert config.sslmode == "prefer"


def test_config_default_timeout():
    """Test default timeout value."""
    config = PostgreSQLConfig(username="test_user")

    assert config.timeout_seconds == 30


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "host": "localhost",
        "port": 5432,
        "username": "postgres",
        "password": "pass123",
        "database": "testdb",
        "schema_name": "public",
    }

    config = PostgreSQLConfig(**config_dict)

    assert config.host == "localhost"
    assert config.port == 5432
    assert config.username == "postgres"
    assert config.password == "pass123"
    assert config.database == "testdb"
    assert config.schema_name == "public"


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = PostgreSQLConfig(
        host="localhost",
        port=5432,
        username="postgres",
        password="pass123",
        database="testdb",
    )

    config_dict = config.model_dump()

    assert config_dict["host"] == "localhost"
    assert config_dict["port"] == 5432
    assert config_dict["username"] == "postgres"
    assert config_dict["password"] == "pass123"
    assert config_dict["database"] == "testdb"
    assert config_dict["schema_name"] == "public"
    assert config_dict["sslmode"] == "prefer"
    assert config_dict["timeout_seconds"] == 30


@pytest.mark.acceptance
def test_config_special_characters_in_password():
    """Test config with special characters in password."""
    special_password = "p@ss!w0rd#$%^&*()"
    config = PostgreSQLConfig(username="test_user", password=special_password)

    assert config.password == special_password


def test_config_special_characters_in_database():
    """Test config with special characters in database name."""
    special_db = "test-db_123"
    config = PostgreSQLConfig(username="test_user", database=special_db)

    assert config.database == special_db


def test_config_unicode_in_username():
    """Test config with unicode characters in username."""
    unicode_user = "usér_nâmé_тест"
    config = PostgreSQLConfig(username=unicode_user)

    assert config.username == unicode_user


def test_config_negative_port():
    """Test that negative port values are accepted (no validation)."""
    config = PostgreSQLConfig(username="test_user", port=-1)
    assert config.port == -1


def test_config_zero_timeout():
    """Test that zero timeout is allowed."""
    config = PostgreSQLConfig(username="test_user", timeout_seconds=0)

    assert config.timeout_seconds == 0


def test_config_large_port_number():
    """Test config with large port number."""
    config = PostgreSQLConfig(username="test_user", port=65535)

    assert config.port == 65535


def test_config_port_out_of_range():
    """Test that port out of valid range is accepted (no validation)."""
    config = PostgreSQLConfig(username="test_user", port=70000)
    assert config.port == 70000


def test_config_sslmode_disable():
    """Test config with sslmode=disable."""
    config = PostgreSQLConfig(username="test_user", sslmode="disable")
    assert config.sslmode == "disable"


def test_config_sslmode_require():
    """Test config with sslmode=require."""
    config = PostgreSQLConfig(username="test_user", sslmode="require")
    assert config.sslmode == "require"


def test_config_sslmode_verify_full():
    """Test config with sslmode=verify-full."""
    config = PostgreSQLConfig(username="test_user", sslmode="verify-full")
    assert config.sslmode == "verify-full"


def test_config_enable_vector_search_defaults_true():
    """Test vector search flag defaults to enabled."""
    config = PostgreSQLConfig(username="test_user")
    assert config.enable_vector_search is True


def test_config_enable_vector_search_can_be_disabled():
    """Test vector search flag can be disabled explicitly."""
    config = PostgreSQLConfig(username="test_user", enable_vector_search=False)
    assert config.enable_vector_search is False
