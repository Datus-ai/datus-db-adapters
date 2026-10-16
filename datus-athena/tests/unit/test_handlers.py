from types import SimpleNamespace

import pytest
from sqlglot.errors import ParseError

from datus_athena.handlers import build_athena_uri, parse_athena_identifier, resolve_athena_context


@pytest.mark.parametrize(
    "value,expected",
    [
        ("", ("", "", "")),
        ("orders", ("", "", "orders")),
        ("..orders", ("", "", "orders")),
        (".analytics.orders", ("", "analytics", "orders")),
        ("analytics.orders", ("", "analytics", "orders")),
        ('"My.Catalog"."Mixed DB"."Order"', ("My.Catalog", "Mixed DB", "Order")),
        ('"cat"."db"."a""b"', ("cat", "db", 'a"b')),
        ("`cat`.`db`.`orders`", ("cat", "db", "orders")),
    ],
)
def test_identifier(value, expected):
    parsed = parse_athena_identifier(value)
    assert (parsed["catalog_name"], parsed["database_name"], parsed["table_name"]) == expected
    assert parsed["schema_name"] == ""


@pytest.mark.parametrize("value", ["a.b.c.d", "t alias", '"unterminated', "a.", "a..b", "...table", ".a.b.c", ".."])
def test_invalid_identifier(value):
    with pytest.raises((ValueError, ParseError)):
        parse_athena_identifier(value)


def test_uri_roundtrip_and_no_credential_leak():
    config = SimpleNamespace(
        catalog="My.Catalog",
        database="data/base",
        password="do-not-leak",
        extra={"region": "us-east-1", "workgroup": "analytics", "profile_name": "role"},
    )
    uri = build_athena_uri(config)
    assert uri == "athena://My.Catalog/data%2Fbase?workgroup=analytics&region=us-east-1&profile_name=role"
    assert "do-not-leak" not in uri
    assert resolve_athena_context(config, uri) == ("athena", "My.Catalog", "data/base", "")
    assert resolve_athena_context(SimpleNamespace(), "athena:///") == ("athena", "AwsDataCatalog", "", "")
