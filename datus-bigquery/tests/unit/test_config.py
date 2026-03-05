# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from datus_bigquery import BigQueryConfig
from pydantic import ValidationError


@pytest.mark.acceptance
def test_config_with_all_required_fields():
    """Test config initialization with only the required project field."""
    config = BigQueryConfig(project="my-gcp-project")

    assert config.project == "my-gcp-project"
    assert config.dataset is None
    assert config.credentials_path is None
    assert config.location is None
    assert config.timeout_seconds == 60


@pytest.mark.acceptance
def test_config_with_custom_values():
    """Test config with all custom values."""
    config = BigQueryConfig(
        project="my-project-123",
        dataset="analytics",
        credentials_path="/path/to/credentials.json",
        location="US",
        timeout_seconds=120,
    )

    assert config.project == "my-project-123"
    assert config.dataset == "analytics"
    assert config.credentials_path == "/path/to/credentials.json"
    assert config.location == "US"
    assert config.timeout_seconds == 120


@pytest.mark.acceptance
def test_config_missing_required_field():
    """Test that validation fails when required project field is missing."""
    with pytest.raises(ValidationError) as exc_info:
        BigQueryConfig()

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("project",)
    assert errors[0]["type"] == "missing"


def test_config_invalid_timeout_type():
    """Test that validation fails for invalid timeout type."""
    with pytest.raises(ValidationError) as exc_info:
        BigQueryConfig(project="my-project", timeout_seconds="invalid")

    errors = exc_info.value.errors()
    assert any(error["loc"] == ("timeout_seconds",) for error in errors)


@pytest.mark.acceptance
def test_config_forbids_extra_fields():
    """Test that extra fields are not allowed."""
    with pytest.raises(ValidationError) as exc_info:
        BigQueryConfig(project="my-project", extra_field="not_allowed")

    errors = exc_info.value.errors()
    assert any(error["type"] == "extra_forbidden" for error in errors)


def test_config_with_none_dataset():
    """Test config with None as dataset."""
    config = BigQueryConfig(project="my-project", dataset=None)

    assert config.dataset is None


def test_config_default_timeout():
    """Test default timeout value."""
    config = BigQueryConfig(project="my-project")

    assert config.timeout_seconds == 60


def test_config_from_dict():
    """Test creating config from dictionary."""
    config_dict = {
        "project": "my-project",
        "dataset": "my_dataset",
        "credentials_path": "/tmp/creds.json",
        "location": "EU",
    }

    config = BigQueryConfig(**config_dict)

    assert config.project == "my-project"
    assert config.dataset == "my_dataset"
    assert config.credentials_path == "/tmp/creds.json"
    assert config.location == "EU"


def test_config_to_dict():
    """Test converting config to dictionary."""
    config = BigQueryConfig(
        project="my-project",
        dataset="analytics",
        location="US",
    )

    config_dict = config.model_dump()

    assert config_dict["project"] == "my-project"
    assert config_dict["dataset"] == "analytics"
    assert config_dict["credentials_path"] is None
    assert config_dict["location"] == "US"
    assert config_dict["timeout_seconds"] == 60


def test_config_with_none_credentials():
    """Test config with None credentials_path."""
    config = BigQueryConfig(project="my-project", credentials_path=None)

    assert config.credentials_path is None


def test_config_with_none_location():
    """Test config with None location."""
    config = BigQueryConfig(project="my-project", location=None)

    assert config.location is None


def test_config_zero_timeout():
    """Test that zero timeout is allowed."""
    config = BigQueryConfig(project="my-project", timeout_seconds=0)

    assert config.timeout_seconds == 0


def test_config_special_characters_in_project():
    """Test config with special characters in project name."""
    special_project = "my-project-123"
    config = BigQueryConfig(project=special_project)

    assert config.project == special_project


def test_config_special_characters_in_dataset():
    """Test config with special characters in dataset name."""
    special_dataset = "test_dataset_123"
    config = BigQueryConfig(project="my-project", dataset=special_dataset)

    assert config.dataset == special_dataset


def test_config_various_locations():
    """Test config with various location values."""
    for location in ["US", "EU", "asia-east1", "us-central1"]:
        config = BigQueryConfig(project="my-project", location=location)
        assert config.location == location
