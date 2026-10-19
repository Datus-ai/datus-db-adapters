from types import SimpleNamespace

import pytest
from sqlglot.errors import ParseError

from datus_athena import AthenaConfig
from datus_athena.handlers import build_athena_uri, parse_athena_identifier, resolve_athena_context
from datus_db_core.aws import AWSIdentityConfig


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


@pytest.mark.parametrize(
    "config",
    [
        SimpleNamespace(extra={"aws": {"region": "us-east-1", "profile": "role"}}),
        SimpleNamespace(aws=AWSIdentityConfig(region="us-east-1", profile="role")),
        AthenaConfig(aws={"region": "us-east-1", "profile": "role"}),
    ],
)
def test_nested_aws_identity_has_the_same_uri_as_flat_fields(config):
    flat = SimpleNamespace(extra={"region": "us-east-1", "profile_name": "role"})
    assert build_athena_uri(config) == build_athena_uri(flat)
    assert build_athena_uri(config) == "athena://AwsDataCatalog/?workgroup=primary&region=us-east-1&profile_name=role"


def test_nested_regions_and_profiles_produce_distinct_identities():
    identities = [("us-east-1", "role"), ("us-west-2", "role"), ("us-east-1", "other-role")]
    uris = {
        build_athena_uri(SimpleNamespace(extra={"aws": dict(region=region, profile=profile)}))
        for region, profile in identities
    }
    assert len(uris) == 3


@pytest.mark.parametrize("flat", [{"region": "us-west-2"}, {"profile_name": "other-role"}])
def test_uri_rejects_conflicting_flat_and_nested_aws_identity(flat):
    config = SimpleNamespace(extra=dict(flat, aws={"region": "us-east-1", "profile": "role"}))
    with pytest.raises(ValueError, match="Conflicting"):
        build_athena_uri(config)


@pytest.mark.parametrize("top_level", [False, True])
@pytest.mark.parametrize("database", [" analytics ", " "])
def test_uri_and_context_match_normalized_connector_configuration(top_level, database):
    values = dict(catalog=" My.Catalog ", database=database, workgroup=" analytics ", region=" us-east-1 ")
    config = SimpleNamespace(**values) if top_level else SimpleNamespace(extra=values)
    native = AthenaConfig(**values)
    assert build_athena_uri(config) == build_athena_uri(native)
    expected = ("athena", native.catalog, native.database or "", "")
    assert resolve_athena_context(config, build_athena_uri(config)) == expected
    assert resolve_athena_context(config, "athena:///") == expected


@pytest.mark.parametrize("name", ["catalog", "workgroup"])
@pytest.mark.parametrize("value", ["", " "])
def test_uri_rejects_blank_required_fields_like_the_connector(name, value):
    with pytest.raises(ValueError):
        build_athena_uri(SimpleNamespace(extra={name: value}))


@pytest.mark.parametrize("extra", [{}, {"catalog": " My.Catalog ", "database": " analytics "}])
def test_agent_empty_default_attributes_leave_defaults_or_extra_in_effect(extra):
    config = SimpleNamespace(catalog="", database="", extra=extra)
    native = AthenaConfig(**extra)
    assert build_athena_uri(config) == build_athena_uri(native)
    assert resolve_athena_context(config, "athena:///") == ("athena", native.catalog, native.database or "", "")


def test_supplied_uri_context_uses_the_same_normalization():
    assert resolve_athena_context(SimpleNamespace(), "athena://%20My.Catalog%20/%20analytics%20") == (
        "athena",
        "My.Catalog",
        "analytics",
        "",
    )
