# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig
from datus_doris.handlers import build_doris_uri, parse_doris_identifier, resolve_doris_context


def _config(**overrides):
    values = {"host": "127.0.0.1", "port": 9030, "username": "root", "database": "analytics"}
    values.update(overrides)
    return DorisConfig(**values)


def test_build_uri_carries_catalog_and_database():
    uri = build_doris_uri(_config(catalog="hive_catalog"))

    assert uri.startswith("doris://127.0.0.1:9030/analytics?")
    assert "catalog=hive_catalog" in uri


def test_build_uri_defaults_catalog_to_internal():
    assert "catalog=internal" in build_doris_uri(_config())


def test_build_uri_omits_credentials():
    uri = build_doris_uri(_config(username="root", password="s3cret"))

    assert "s3cret" not in uri
    assert "root" not in uri


def test_build_uri_allows_missing_database():
    uri = build_doris_uri(_config(database=None))

    assert uri.startswith("doris://127.0.0.1:9030/?")


def test_build_uri_rejects_scheme_in_host():
    with pytest.raises(ValueError, match="must not include a URI scheme"):
        build_doris_uri(_config(host="doris://127.0.0.1"))


def test_build_uri_escapes_database_name():
    assert "/a%2Fb?" in build_doris_uri(_config(database="a/b"))


def test_resolve_context_prefers_uri_over_config():
    config = _config(catalog="internal", database="analytics")
    uri = "doris://127.0.0.1:9030/warehouse?catalog=hive_catalog"

    assert resolve_doris_context(config, uri) == ("doris", "hive_catalog", "warehouse", "")


def test_resolve_context_falls_back_to_config():
    config = _config(catalog="hive_catalog", database="analytics")

    assert resolve_doris_context(config, "") == ("doris", "hive_catalog", "analytics", "")


def test_resolve_context_normalizes_placeholder_catalog():
    """``def`` is the placeholder the MySQL protocol reports for TABLE_CATALOG."""
    config = _config(catalog="def")

    assert resolve_doris_context(config, "")[1] == "internal"


def test_resolve_context_leaves_schema_empty():
    """Doris has no schema level between database and table."""
    assert resolve_doris_context(_config(), "")[3] == ""


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("t", {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": "t"}),
        ("db.t", {"catalog_name": "", "database_name": "db", "schema_name": "", "table_name": "t"}),
        (
            "hive_catalog.db.t",
            {"catalog_name": "hive_catalog", "database_name": "db", "schema_name": "", "table_name": "t"},
        ),
        (
            "`hive catalog`.`my.db`.`t`",
            {"catalog_name": "hive catalog", "database_name": "my.db", "schema_name": "", "table_name": "t"},
        ),
        ("", {"catalog_name": "", "database_name": "", "schema_name": "", "table_name": ""}),
    ],
)
def test_parse_identifier(identifier, expected):
    assert parse_doris_identifier(identifier) == expected


@pytest.mark.parametrize("identifier", ["a.b.c.d", "db.", ".t", "`unterminated"])
def test_parse_identifier_rejects_malformed_input(identifier):
    with pytest.raises(ValueError, match="Invalid Doris table identifier"):
        parse_doris_identifier(identifier)
