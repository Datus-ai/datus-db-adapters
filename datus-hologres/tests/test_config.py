# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_hologres import HologresConfig


def test_config_defaults():
    config = HologresConfig(username="test_user")

    assert config.host == "127.0.0.1"
    assert config.port == 5432
    assert config.username == "test_user"
    assert config.password == ""
    assert config.database is None
    assert config.schema_name == "public"
    assert config.sslmode == "prefer"
    assert config.timeout_seconds == 30


def test_config_custom_values():
    config = HologresConfig(
        host="hologres.example.com",
        port=5433,
        username="admin",
        password="secret",
        database="analytics",
        schema_name="mart",
        sslmode="require",
        timeout_seconds=60,
    )

    assert config.host == "hologres.example.com"
    assert config.port == 5433
    assert config.username == "admin"
    assert config.password == "secret"
    assert config.database == "analytics"
    assert config.schema_name == "mart"
    assert config.sslmode == "require"
    assert config.timeout_seconds == 60


def test_config_accepts_schema_alias():
    config = HologresConfig(username="test_user", schema="ods")

    assert config.schema_name == "ods"


def test_config_forbids_extra_fields():
    with pytest.raises(ValidationError) as exc_info:
        HologresConfig(username="test_user", unsupported=True)

    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())


def test_config_missing_username():
    with pytest.raises(ValidationError) as exc_info:
        HologresConfig()

    assert any(error["loc"] == ("username",) for error in exc_info.value.errors())


def test_config_model_dump_uses_schema_name():
    config = HologresConfig(username="test_user", schema="public")

    config_dict = config.model_dump()

    assert config_dict["schema_name"] == "public"
    assert "schema" not in config_dict
