import pytest
from pydantic import ValidationError

from datus_bigquery import BigQueryConfig


def test_minimal_config_and_defaults():
    config = BigQueryConfig(project="my-project")

    assert config.project == "my-project"
    assert config.dataset is None
    assert config.timeout_seconds == 60
    assert config.credentials_path is None
    assert config.credentials_info is None
    assert config.credentials_base64 is None


@pytest.mark.acceptance
def test_datus_namespace_aliases_override_adapter_names():
    config = BigQueryConfig(
        project="original-project",
        dataset="original_dataset",
        catalog="request-project",
        database="request_dataset",
    )

    assert config.project == "request-project"
    assert config.dataset == "request_dataset"


def test_optional_strings_are_trimmed_and_blanks_become_none():
    config = BigQueryConfig(
        project="  my-project  ",
        dataset="  analytics  ",
        credentials_path=" ",
        billing_project_id=" quota-project ",
        location=" US ",
    )

    assert config.project == "my-project"
    assert config.dataset == "analytics"
    assert config.credentials_path is None
    assert config.billing_project_id == "quota-project"
    assert config.location == "US"


@pytest.mark.parametrize("project", ["", "   "])
def test_project_must_not_be_empty(project):
    with pytest.raises(ValidationError, match="project must not be empty"):
        BigQueryConfig(project=project)


@pytest.mark.parametrize("timeout", [0, -1])
def test_timeout_must_be_positive(timeout):
    with pytest.raises(ValidationError):
        BigQueryConfig(project="my-project", timeout_seconds=timeout)


def test_only_one_credentials_mechanism_is_allowed():
    with pytest.raises(ValidationError, match="Configure only one"):
        BigQueryConfig(
            project="my-project",
            credentials_path="/tmp/credentials.json",
            credentials_info={"type": "service_account"},
        )


def test_inline_credentials_are_secret_in_repr_and_dump():
    config = BigQueryConfig(
        project="my-project",
        credentials_info={"type": "service_account", "private_key": "super-secret"},
    )

    assert "super-secret" not in repr(config)
    assert "super-secret" not in str(config.model_dump())
    assert config.credentials_info.get_secret_value()["private_key"] == "super-secret"


def test_base64_credentials_are_secret():
    config = BigQueryConfig(project="my-project", credentials_base64="encoded-secret")

    assert "encoded-secret" not in repr(config)
    assert config.credentials_base64.get_secret_value() == "encoded-secret"


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError) as exc_info:
        BigQueryConfig(project="my-project", typo="value")

    assert any(error["type"] == "extra_forbidden" for error in exc_info.value.errors())
