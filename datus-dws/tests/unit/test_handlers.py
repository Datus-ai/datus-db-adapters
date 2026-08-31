# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from urllib.parse import parse_qs, urlparse

import pytest

from datus_dws import DWSConfig
from datus_dws.handlers import build_dws_uri, parse_dws_identifier, resolve_dws_context


def _config(**overrides):
    values = {
        "host": "example.dws.myhuaweicloud.com",
        "username": "dbadmin",
        "password": "secret-value",
        "database": "gaussdb",
    }
    values.update(overrides)
    return DWSConfig(**values)


def test_uri_is_credential_free_and_carries_context():
    uri = build_dws_uri(_config(schema="reporting", sslmode="verify-ca"))

    assert uri.startswith("dws://example.dws.myhuaweicloud.com:8000/gaussdb?")
    assert "secret-value" not in uri
    assert "dbadmin" not in uri

    params = parse_qs(urlparse(uri).query)
    assert params["schema"] == ["reporting"]
    assert params["sslmode"] == ["verify-ca"]


def test_uri_brackets_ipv6_host_so_port_stays_parseable():
    uri = build_dws_uri(_config(host="2001:db8::1"))

    parsed = urlparse(uri)
    assert parsed.hostname == "2001:db8::1"
    assert parsed.port == 8000


def test_uri_percent_encodes_database_name():
    uri = build_dws_uri(_config(database="sales/eu"))

    assert "sales%2Feu" in uri
    assert urlparse(uri).path == "/sales%2Feu"


def test_uri_requires_database():
    class _Bare:
        host = "example.dws.myhuaweicloud.com"
        database = ""
        port = 8000

    with pytest.raises(ValueError, match="database is required"):
        build_dws_uri(_Bare())


def test_context_round_trips_from_uri():
    config = _config(schema="reporting")
    dialect, catalog, database, schema = resolve_dws_context(config, build_dws_uri(config))

    assert dialect == "dws"
    assert catalog == ""
    assert database == "gaussdb"
    assert schema == "reporting"


def test_context_decodes_escaped_database_name():
    config = _config(database="sales/eu")
    _, _, database, _ = resolve_dws_context(config, build_dws_uri(config))

    assert database == "sales/eu"


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("orders", {"database_name": "", "schema_name": "", "table_name": "orders"}),
        ("public.orders", {"database_name": "", "schema_name": "public", "table_name": "orders"}),
        (
            "gaussdb.public.orders",
            {"database_name": "gaussdb", "schema_name": "public", "table_name": "orders"},
        ),
    ],
)
def test_identifier_parses_one_two_and_three_parts(identifier, expected):
    parsed = parse_dws_identifier(identifier)

    assert parsed["catalog_name"] == ""
    for key, value in expected.items():
        assert parsed[key] == value


def test_identifier_rejects_four_parts_because_dws_has_no_catalog():
    with pytest.raises(ValueError, match="Invalid DWS table identifier"):
        parse_dws_identifier("cluster.gaussdb.public.orders")


def test_identifier_unwraps_double_quotes_and_keeps_inner_dot():
    parsed = parse_dws_identifier('"public"."my.table"')

    assert parsed["schema_name"] == "public"
    assert parsed["table_name"] == "my.table"


def test_identifier_collapses_doubled_quote_escape():
    parsed = parse_dws_identifier('"we""ird"')

    assert parsed["table_name"] == 'we"ird'


def test_identifier_rejects_unbalanced_quote():
    with pytest.raises(ValueError, match="Invalid DWS table identifier"):
        parse_dws_identifier('"public.orders')


def test_identifier_rejects_empty_part():
    with pytest.raises(ValueError, match="Invalid DWS table identifier"):
        parse_dws_identifier("public..orders")


def test_identifier_returns_blank_result_for_empty_input():
    parsed = parse_dws_identifier("")

    assert parsed == {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}
