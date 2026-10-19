# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from datus_clickhouse.handlers import build_clickhouse_uri, resolve_clickhouse_context


@pytest.mark.parametrize(
    "protocol,port,expected", [("http", "", 8123), ("https", None, 8443), ("https", "19443", 19443)]
)
def test_context_uri_uses_the_execution_route_and_hides_password(protocol, port, expected):
    config = SimpleNamespace(
        host="clickhouse.example.com",
        port=port,
        username="u",
        password="hidden-secret",
        database="analytics",
        schema="",
        catalog="",
        extra={"protocol": protocol, "ca_cert": "/private/ca.pem"},
    )
    uri = build_clickhouse_uri(config)
    parsed = make_url(uri)
    assert parsed.get_backend_name() == "clickhouse"
    assert parsed.port == expected
    assert parsed.query == {"protocol": protocol}
    assert "hidden-secret" not in uri
    assert "ca.pem" not in uri
    assert resolve_clickhouse_context(config, uri) == ("clickhouse", "", "analytics", "")


def test_invalid_protocol_is_not_rendered_as_an_http_uri():
    config = SimpleNamespace(host="h", port=None, username="u", password="", database=None, extra={"protocol": "tls"})
    with pytest.raises(ValueError, match="protocol"):
        build_clickhouse_uri(config)
