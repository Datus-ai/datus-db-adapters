# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace

import pytest

from datus_hologres.handlers import (
    build_hologres_uri,
    parse_hologres_identifier,
    resolve_hologres_context,
)


def test_build_uri_is_credential_free():
    config = SimpleNamespace(
        host="example.hologres.aliyuncs.com",
        port=80,
        username="access-id",
        password="access-secret",
        database="analytics",
        schema="reporting",
        sslmode="disable",
    )

    uri = build_hologres_uri(config)

    assert uri == "hologres://example.hologres.aliyuncs.com:80/analytics?schema=reporting&sslmode=disable"
    assert "access-id" not in uri
    assert "access-secret" not in uri
    assert resolve_hologres_context(config, uri) == ("hologres", "", "analytics", "reporting")


def test_build_uri_reads_adapter_extra_fields():
    config = SimpleNamespace(
        host="",
        port="",
        database="",
        schema="",
        extra={
            "host": "example.hologres.aliyuncs.com",
            "database": "analytics",
            "schema_name": "reporting",
            "sslmode": "require",
        },
    )

    assert build_hologres_uri(config).endswith("/analytics?schema=reporting&sslmode=require")


def test_build_uri_normalizes_console_endpoint_with_embedded_port():
    config = SimpleNamespace(
        host="example.hologres.aliyuncs.com:81",
        port="",
        database="analytics",
        schema="public",
        sslmode="require",
    )

    assert build_hologres_uri(config) == (
        "hologres://example.hologres.aliyuncs.com:81/analytics?schema=public&sslmode=require"
    )


def test_build_uri_round_trips_reserved_database_characters():
    config = SimpleNamespace(
        host="example.hologres.aliyuncs.com",
        port=80,
        database="analytics/#daily?source",
        schema="public",
        sslmode="require",
    )

    uri = build_hologres_uri(config)

    assert uri == (
        "hologres://example.hologres.aliyuncs.com:80/analytics%2F%23daily%3Fsource?schema=public&sslmode=require"
    )
    assert resolve_hologres_context(config, uri) == (
        "hologres",
        "",
        "analytics/#daily?source",
        "public",
    )


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("orders", ("", "", "orders")),
        ("public.orders", ("", "public", "orders")),
        ("analytics.public.orders", ("analytics", "public", "orders")),
        ('"analytics"."Mixed Schema"."Order.Items"', ("analytics", "Mixed Schema", "Order.Items")),
        ('"analytics"."a""b"."order""items"', ("analytics", 'a"b', 'order"items')),
    ],
)
def test_parse_identifier(identifier, expected):
    parsed = parse_hologres_identifier(identifier)
    assert (parsed["database_name"], parsed["schema_name"], parsed["table_name"]) == expected
    assert parsed["catalog_name"] == ""


@pytest.mark.parametrize("identifier", ["a.b.c.d", "a..b", '"unterminated'])
def test_parse_identifier_rejects_invalid_names(identifier):
    with pytest.raises(ValueError):
        parse_hologres_identifier(identifier)
