from unittest.mock import patch

import pytest
from google.auth.credentials import AnonymousCredentials

from datus_bigquery import BigQueryConnector


def test_query_and_metadata_use_configured_deadlines():
    connector = BigQueryConnector({"project": "test-project", "dataset": "test", "timeout_seconds": 7})
    with patch("google.auth.default", return_value=(AnonymousCredentials(), "test-project")) as auth:
        options = connector._engine_kwargs()
    assert set(auth.call_args.kwargs["scopes"]) == {
        "https://www.googleapis.com/auth/bigquery",
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/drive",
    }
    client = options.get("connect_args", {}).get("client")
    assert client is not None
    try:
        with patch.object(
            client._connection,
            "api_request",
            return_value={"tableReference": {"projectId": "test-project", "datasetId": "test", "tableId": "t"}},
        ) as request:
            client.get_table("test-project.test.t")
        assert request.call_args.kwargs["timeout"] == 7
        assert int(client.default_query_job_config.job_timeout_ms) == 7000
        with patch("google.cloud.bigquery.client._job_helpers.query_and_wait", return_value=[]) as wait:
            client.query_and_wait("SELECT 1")
        assert wait.call_args.kwargs["wait_timeout"] == 7
        assert wait.call_args.kwargs["api_timeout"] == 7
        assert wait.call_args.kwargs["job_retry"] is None
    finally:
        client.close()
        connector.close()


@pytest.mark.parametrize("field", ["timeout_seconds", "query_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_bigquery import BigQueryConfig

    with pytest.raises(ValidationError):
        BigQueryConfig(**{"project": "test-project"}, **{field: value})


def test_context_override_retargets_sdk_billing_project_and_default_dataset():
    from sqlalchemy import create_engine

    connector = BigQueryConnector({"project": "initial-project", "dataset": "initial"})
    try:
        with (
            patch("google.auth.default", return_value=(AnonymousCredentials(), "initial-project")),
            patch("datus_bigquery.connector.create_engine", wraps=create_engine) as create,
        ):
            connector._get_engine("other-project", "other_dataset")
        client = create.call_args.kwargs["connect_args"]["client"]
        assert client.project == "other-project"
        assert str(client.default_query_job_config.default_dataset) == "other-project.other_dataset"
    finally:
        connector.close()
