# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import pytest
from pydantic import ValidationError

from datus_maxcompute import MaxComputeConfig


def test_config_accepts_database_alias_and_hides_secrets():
    config = MaxComputeConfig(
        database="project_a",
        endpoint="https://service.example/api",
        access_key_id="id-value",
        access_key_secret="secret-value",
    )

    assert config.project == "project_a"
    assert config.namespace_mode == "auto"
    assert config.query_timeout_seconds == 600
    assert "secret-value" not in repr(config)
    assert "id-value" not in repr(config)


def test_config_accepts_schema_alias():
    config = MaxComputeConfig(
        project="project_a",
        endpoint="https://service.example/api",
        access_key_id="id",
        access_key_secret="secret",
        schema="analytics",
    )

    assert config.schema_name == "analytics"


@pytest.mark.parametrize("field", ["project", "endpoint"])
@pytest.mark.parametrize("empty_value", ["", " "])
def test_config_rejects_empty_required_strings(field, empty_value):
    values = {
        "project": "project_a",
        "endpoint": "https://service.example/api",
        "access_key_id": "id",
        "access_key_secret": "secret",
    }
    values[field] = empty_value
    with pytest.raises(ValidationError):
        MaxComputeConfig(**values)


def test_config_rejects_unknown_namespace_mode():
    with pytest.raises(ValidationError):
        MaxComputeConfig(
            project="project_a",
            endpoint="https://service.example/api",
            access_key_id="id",
            access_key_secret="secret",
            namespace_mode="guess",
        )
