# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from datus_gaussdb import GaussDBConfig
from datus_gaussdb.handlers import (
    build_gaussdb_uri,
    parse_gaussdb_identifier,
    resolve_gaussdb_context,
)

# ==================== build_gaussdb_uri ====================


@pytest.mark.acceptance
def test_build_uri_defaults():
    """A minimal config yields host/port/database plus schema and sslmode params."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="postgres")

    uri = build_gaussdb_uri(config)

    parsed = urlparse(uri)
    assert parsed.scheme == "gaussdb"
    assert parsed.netloc == "gauss.internal:5432"
    assert parsed.path == "/postgres"
    assert parse_qs(parsed.query) == {"schema": ["public"], "sslmode": ["prefer"]}


@pytest.mark.acceptance
def test_build_uri_is_credential_free():
    """Datasource identity must not embed username or password."""
    config = GaussDBConfig(
        username="datus",
        password="Datus@123",
        host="gauss.internal",
        database="postgres",
    )

    uri = build_gaussdb_uri(config)

    assert "datus" not in uri
    assert "Datus@123" not in uri
    assert "@" not in uri


@pytest.mark.acceptance
def test_build_uri_uses_configured_schema_and_sslmode():
    """schema and sslmode come from the config, not the defaults."""
    config = GaussDBConfig(
        username="datus",
        host="gauss.internal",
        port=25434,
        database="analyticsdb",
        schema="analytics",
        sslmode="require",
    )

    uri = build_gaussdb_uri(config)

    assert uri.startswith("gaussdb://gauss.internal:25434/analyticsdb?")
    assert parse_qs(urlparse(uri).query) == {"schema": ["analytics"], "sslmode": ["require"]}


@pytest.mark.acceptance
def test_build_uri_quotes_database_name():
    """Database names with URI-significant characters are percent-encoded."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="my db/prod")

    uri = build_gaussdb_uri(config)

    assert "/my%20db%2Fprod?" in uri
    assert urlparse(uri).path == "/my%20db%2Fprod"


@pytest.mark.acceptance
def test_build_uri_requires_host():
    """A blank host is a configuration error, not a silently empty URI."""
    config = GaussDBConfig(username="datus", host="", database="postgres")

    with pytest.raises(ValueError, match="host is required"):
        build_gaussdb_uri(config)


@pytest.mark.acceptance
def test_build_uri_requires_database():
    """database defaults to None on the config and must be set explicitly."""
    config = GaussDBConfig(username="datus", host="gauss.internal")

    with pytest.raises(ValueError, match="database is required"):
        build_gaussdb_uri(config)


@pytest.mark.acceptance
def test_build_uri_reads_extra_dict_fallback():
    """Values missing as attributes are read from an 'extra' mapping."""
    config = SimpleNamespace(extra={"host": "gauss.internal", "database": "postgres", "schema": "ods"})

    uri = build_gaussdb_uri(config)

    assert uri == "gaussdb://gauss.internal:5432/postgres?schema=ods&sslmode=prefer"


@pytest.mark.acceptance
def test_build_uri_brackets_ipv6_hosts():
    """An unbracketed IPv6 literal makes the authority's port unparseable."""
    config = GaussDBConfig(username="datus", host="::1", database="postgres")

    parsed = urlparse(build_gaussdb_uri(config))

    assert parsed.netloc == "[::1]:5432"
    assert parsed.hostname == "::1"
    assert parsed.port == 5432


@pytest.mark.acceptance
def test_build_uri_keeps_already_bracketed_ipv6_hosts():
    """A host that already carries brackets is not double-wrapped."""
    config = GaussDBConfig(username="datus", host="[fe80::1]", database="postgres")

    assert urlparse(build_gaussdb_uri(config)).netloc == "[fe80::1]:5432"


# ==================== resolve_gaussdb_context ====================


@pytest.mark.acceptance
def test_resolve_context_from_uri():
    """The URI wins over the config when it carries database and schema."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="ignored", schema="ignored_schema")

    context = resolve_gaussdb_context(config, "gaussdb://gauss.internal:25434/analyticsdb?schema=analytics")

    assert context == ("gaussdb", "", "analyticsdb", "analytics")


@pytest.mark.acceptance
def test_resolve_context_has_no_catalog_level():
    """GaussDB has no catalog level, so the second slot is always empty."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="postgres")

    dialect, catalog, database, schema = resolve_gaussdb_context(config, build_gaussdb_uri(config))

    assert dialect == "gaussdb"
    assert catalog == ""
    assert database == "postgres"
    assert schema == "public"


@pytest.mark.acceptance
def test_resolve_context_falls_back_to_config():
    """An empty URI falls back to the configured database and schema."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="postgres", schema="ods")

    assert resolve_gaussdb_context(config, "") == ("gaussdb", "", "postgres", "ods")


@pytest.mark.acceptance
def test_resolve_context_defaults_schema_to_public():
    """Without a schema anywhere, the context resolves to 'public'."""
    config = SimpleNamespace(database="postgres")

    assert resolve_gaussdb_context(config, "gaussdb://gauss.internal:5432/postgres") == (
        "gaussdb",
        "",
        "postgres",
        "public",
    )


@pytest.mark.acceptance
def test_resolve_context_unquotes_database():
    """A percent-encoded database path is decoded back to its real name."""
    config = GaussDBConfig(username="datus", host="gauss.internal", database="fallback")

    _, _, database, _ = resolve_gaussdb_context(config, "gaussdb://gauss.internal:5432/my%20db?schema=public")

    assert database == "my db"


# ==================== parse_gaussdb_identifier ====================


@pytest.mark.acceptance
def test_parse_identifier_table_only():
    """A bare table name leaves schema and database empty."""
    assert parse_gaussdb_identifier("orders") == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": "",
        "table_name": "orders",
    }


@pytest.mark.acceptance
def test_parse_identifier_schema_qualified():
    """schema.table populates schema_name."""
    assert parse_gaussdb_identifier("public.orders") == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": "public",
        "table_name": "orders",
    }


@pytest.mark.acceptance
def test_parse_identifier_database_qualified():
    """database.schema.table populates all three levels; catalog stays empty."""
    assert parse_gaussdb_identifier("postgres.public.orders") == {
        "catalog_name": "",
        "database_name": "postgres",
        "schema_name": "public",
        "table_name": "orders",
    }


@pytest.mark.acceptance
def test_parse_identifier_strips_double_quotes():
    """Quoted identifiers are unwrapped."""
    assert parse_gaussdb_identifier('"public"."Orders"') == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": "public",
        "table_name": "Orders",
    }


@pytest.mark.acceptance
def test_parse_identifier_keeps_dots_inside_quotes():
    """A dot inside quotes is part of the name, not a separator."""
    assert parse_gaussdb_identifier('"my.schema"."my.table"') == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": "my.schema",
        "table_name": "my.table",
    }


@pytest.mark.acceptance
def test_parse_identifier_rejects_four_parts():
    """GaussDB addressing tops out at database.schema.table."""
    with pytest.raises(ValueError, match="Invalid GaussDB table identifier"):
        parse_gaussdb_identifier("a.b.c.d")


@pytest.mark.acceptance
def test_parse_identifier_rejects_empty_part():
    """A dangling separator is a malformed identifier."""
    with pytest.raises(ValueError, match="Invalid GaussDB table identifier"):
        parse_gaussdb_identifier("public.")


@pytest.mark.acceptance
def test_parse_identifier_empty_string():
    """An empty identifier yields empty values rather than raising."""
    assert parse_gaussdb_identifier("") == {
        "catalog_name": "",
        "database_name": "",
        "schema_name": "",
        "table_name": "",
    }
