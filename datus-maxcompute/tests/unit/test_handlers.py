# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace

import pytest

from datus_maxcompute.handlers import (
    build_maxcompute_uri,
    parse_maxcompute_identifier,
    resolve_maxcompute_context,
)


def test_uri_is_credential_free_and_preserves_context():
    config = SimpleNamespace(
        project="project_a",
        endpoint="https://service.example/api",
        schema_name="analytics",
        access_key_id="sensitive-id",
        access_key_secret="sensitive-secret",
    )

    uri = build_maxcompute_uri(config)

    assert "sensitive" not in uri
    assert resolve_maxcompute_context(config, uri) == ("maxcompute", "", "project_a", "analytics")


def test_uri_reads_adapter_fields_from_agent_extra():
    config = SimpleNamespace(
        database="project_a",
        schema="",
        extra={"endpoint": "https://service.example/api"},
    )

    uri = build_maxcompute_uri(config)

    assert "service.example" in uri
    assert resolve_maxcompute_context(config, uri) == ("maxcompute", "", "project_a", "")


def test_uri_strips_endpoint_query_and_fragment():
    config = SimpleNamespace(
        project="project_a",
        endpoint="https://service.example/api?token=sensitive#fragment",
        schema_name="",
    )

    uri = build_maxcompute_uri(config)

    assert "sensitive" not in uri
    assert "fragment" not in uri
    assert "https%3A%2F%2Fservice.example%2Fapi" in uri


def test_uri_rejects_endpoint_userinfo():
    config = SimpleNamespace(
        project="project_a",
        endpoint="https://id:secret@service.example/api",
        schema_name="",
    )

    with pytest.raises(ValueError, match="user information"):
        build_maxcompute_uri(config)


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("orders", ("", "", "orders")),
        ("project_a.orders", ("project_a", "", "orders")),
        ("project_a.analytics.orders", ("project_a", "analytics", "orders")),
        ("`project.a`.`analytics`.`orders`", ("project.a", "analytics", "orders")),
    ],
)
def test_parse_identifier_supports_both_namespace_models(identifier, expected):
    parsed = parse_maxcompute_identifier(identifier)
    assert (parsed["database_name"], parsed["schema_name"], parsed["table_name"]) == expected


def test_parse_identifier_rejects_more_than_three_levels():
    with pytest.raises(ValueError, match="Invalid MaxCompute"):
        parse_maxcompute_identifier("catalog.project.schema.table")


@pytest.mark.parametrize(
    "identifier",
    [
        "project..orders",
        ".orders",
        "project.",
        "`project.orders",
        '"project.orders',
        "[project.orders",
    ],
)
def test_parse_identifier_rejects_malformed_components(identifier):
    with pytest.raises(ValueError, match="Invalid MaxCompute"):
        parse_maxcompute_identifier(identifier)
