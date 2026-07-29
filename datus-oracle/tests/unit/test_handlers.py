# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest
from datus_oracle import OracleConfig
from datus_oracle.handlers import build_oracle_uri, resolve_oracle_context


@pytest.mark.acceptance
def test_uri_with_service_name():
    config = OracleConfig(
        host="db.example.com", port=1521, username="datus_test", password="pw", service_name="FREEPDB1"
    )
    uri = build_oracle_uri(config)
    assert uri == "oracle+oracledb://datus_test:pw@db.example.com:1521?service_name=FREEPDB1"


def test_uri_with_sid():
    config = OracleConfig(host="db.example.com", username="u", password="pw", sid="XE")
    uri = build_oracle_uri(config)
    assert uri == "oracle+oracledb://u:pw@db.example.com:1521/XE"


def test_uri_with_dsn():
    config = OracleConfig(username="u", password="pw", dsn="prod_alias")
    uri = build_oracle_uri(config)
    assert uri == "oracle+oracledb://u:pw@prod_alias"


@pytest.mark.acceptance
def test_uri_encodes_special_characters():
    from sqlalchemy.engine.url import make_url

    config = OracleConfig(username="user@corp", password="p@ss:w/rd#1", service_name="FREEPDB1")
    uri = build_oracle_uri(config)
    assert "user%40corp" in uri
    # The URI must round-trip through SQLAlchemy's parser unchanged
    url = make_url(uri)
    assert url.username == "user@corp"
    assert url.password == "p@ss:w/rd#1"
    assert url.host == "127.0.0.1"
    assert url.query["service_name"] == "FREEPDB1"


def test_uri_without_password():
    config = OracleConfig(username="u", service_name="S")
    uri = build_oracle_uri(config)
    assert uri == "oracle+oracledb://u@127.0.0.1:1521?service_name=S"


@pytest.mark.acceptance
def test_resolve_context_with_schema():
    config = OracleConfig(username="datus_test", service_name="FREEPDB1", schema_name="SALES")
    dialect, catalog, database, schema = resolve_oracle_context(config, "")
    assert dialect == "oracle"
    assert catalog == ""
    assert database == ""
    assert schema == "SALES"


def test_resolve_context_defaults_to_upper_username():
    config = OracleConfig(username="datus_test", service_name="FREEPDB1")
    _, _, _, schema = resolve_oracle_context(config, "")
    assert schema == "DATUS_TEST"
