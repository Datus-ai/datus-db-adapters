from types import SimpleNamespace

import pytest

from datus_bigquery.handlers import build_bigquery_uri, parse_bigquery_identifier, resolve_bigquery_context


def test_uri_is_stable_and_never_contains_credentials():
    config = SimpleNamespace(
        project="my-project",
        dataset="analytics data",
        location="US",
        billing_project_id="quota-project",
        credentials_info={"private_key": "secret"},
    )

    uri = build_bigquery_uri(config)

    assert uri == "bigquery://my-project/analytics%20data?location=US&billing_project_id=quota-project"
    assert "secret" not in uri
    assert resolve_bigquery_context(config, uri) == ("bigquery", "my-project", "analytics data", "")


def test_uri_reads_datus_names_from_extra():
    config = SimpleNamespace(extra={"catalog": "project-a", "database": "dataset_a"})

    assert build_bigquery_uri(config) == "bigquery://project-a/dataset_a"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("events", ("", "", "events")),
        ("analytics.events", ("", "analytics", "events")),
        ("my-project.analytics.events", ("my-project", "analytics", "events")),
        ("`my-project.analytics.events`", ("my-project", "analytics", "events")),
        ("`my-project`.`analytics`.`events`", ("my-project", "analytics", "events")),
    ],
)
def test_identifier_parser_supports_bigquery_quoting(identifier, expected):
    parsed = parse_bigquery_identifier(identifier)

    assert (parsed["catalog_name"], parsed["database_name"], parsed["table_name"]) == expected
    assert parsed["schema_name"] == ""


@pytest.mark.parametrize("identifier", ["a.b.c.d", "a..b", "`unterminated"])
def test_identifier_parser_rejects_invalid_paths(identifier):
    with pytest.raises(ValueError, match="Invalid BigQuery"):
        parse_bigquery_identifier(identifier)
