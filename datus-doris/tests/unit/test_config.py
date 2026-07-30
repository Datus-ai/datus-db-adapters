# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from pydantic import ValidationError

from datus_doris import DorisConfig


@pytest.mark.acceptance
def test_config_defaults():
    config = DorisConfig(username="test_user")

    assert config.model_dump() == {
        "host": "127.0.0.1",
        "port": 9030,
        "username": "test_user",
        "password": "",
        "catalog": "internal",
        "database": None,
        "charset": "utf8mb4",
        "autocommit": True,
        "timeout_seconds": 30,
    }


@pytest.mark.acceptance
def test_config_custom_values():
    config = DorisConfig(
        host="192.168.1.100",
        port=9031,
        username="admin",
        password="p@ss!w0rd",
        catalog="hive-catalog",
        database="analytics",
        charset="utf8",
        autocommit=False,
        timeout_seconds=60,
    )

    assert config.model_dump() == {
        "host": "192.168.1.100",
        "port": 9031,
        "username": "admin",
        "password": "p@ss!w0rd",
        "catalog": "hive-catalog",
        "database": "analytics",
        "charset": "utf8",
        "autocommit": False,
        "timeout_seconds": 60,
    }


def test_config_requires_username():
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig()

    assert exc_info.value.errors()[0]["loc"] == ("username",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("port", "invalid"),
        ("timeout_seconds", "invalid"),
        ("autocommit", "invalid"),
    ],
)
def test_config_rejects_invalid_field_types(field, value):
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", **{field: value})

    assert any(error["loc"] == (field,) for error in exc_info.value.errors())


def test_config_forbids_extra_fields():
    with pytest.raises(ValidationError) as exc_info:
        DorisConfig(username="test_user", extra_field="not_allowed")

    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())
