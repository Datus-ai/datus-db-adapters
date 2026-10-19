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


def _values(**overrides):
    values = {
        "project": "project_a",
        "endpoint": "https://service.example/api",
        "access_key_id": "id",
        "access_key_secret": "secret",
    }
    values.update(overrides)
    return values


def test_config_lists_every_table_by_default():
    """Table filtering is disabled unless the datasource configures patterns."""
    config = MaxComputeConfig(**_values())

    assert config.ignore_table_patterns == []


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (["tmp_*", "*_bak"], ["tmp_*", "*_bak"]),
        ("tmp_*", ["tmp_*"]),
        ([" tmp_* ", "", "  "], ["tmp_*"]),
        (None, []),
        ([], []),
    ],
)
def test_config_normalizes_ignore_table_patterns(given, expected):
    """Accept a pattern string or list and discard blank entries."""
    config = MaxComputeConfig(**_values(ignore_table_patterns=given))

    assert config.ignore_table_patterns == expected


def test_config_rejects_undeclared_fields():
    """Reject datasource options that the adapter configuration does not declare."""
    with pytest.raises(ValidationError):
        MaxComputeConfig(**_values(not_a_real_option=["tmp_*"]))
