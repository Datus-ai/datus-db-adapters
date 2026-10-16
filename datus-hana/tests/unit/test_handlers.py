from types import SimpleNamespace

import pytest

from datus_hana.handlers import build_hana_uri, parse_hana_identifier, quote_identifier, resolve_hana_context


@pytest.mark.parametrize(
    "raw,schema,table",
    [
        ("orders", "", "ORDERS"),
        ('sales."Order.Item"', "SALES", "Order.Item"),
        ('"A.B"."x""y"', "A.B", 'x"y'),
    ],
)
def test_identifiers(raw, schema, table):
    assert parse_hana_identifier(raw) == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": schema,
        "table_name": table,
    }


@pytest.mark.parametrize("raw", ["", "a.b.c", "f(1)", '"broken', "a; b", "a.", "a x", '""'])
def test_invalid_identifiers(raw):
    with pytest.raises(ValueError):
        parse_hana_identifier(raw)


def test_scope_wildcards_are_identifiers_not_sql():
    assert parse_hana_identifier('"Sales".Order*')["table_name"] == "ORDER*"
    assert parse_hana_identifier('"Sales".*')["schema_name"] == "Sales"


def test_uri_and_context_exclude_auth_and_tenant_namespace():
    config = SimpleNamespace(
        host="::1",
        port=443,
        username="private-user",
        password="never-include",
        database="TENANT",
        extra={"schema": "CaseSensitive", "userkey": "PRIVATE_KEY"},
    )
    assert build_hana_uri(config) == "hana://[::1]:443"
    assert resolve_hana_context(config, build_hana_uri(config)) == ("hana", "", "", "CaseSensitive")
    assert quote_identifier('a"b') == '"a""b"'


@pytest.mark.parametrize(
    "fields,schema",
    [
        ({"username": "MixedUser"}, "MixedUser"),
        ({"username": "U", "schema_name": "Explicit"}, "Explicit"),
        ({"username": "U", "schema": "Alias"}, "Alias"),
        ({"username": "U", "extra": {"schema": "Extra"}}, "Extra"),
        ({"extra": {"userkey": "KEY"}}, ""),
    ],
)
def test_context_schema_follows_connector_default(fields, schema):
    assert resolve_hana_context(SimpleNamespace(**fields), "hana://h:39041") == ("hana", "", "", schema)
