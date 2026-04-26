# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import patch

import pytest

from datus_hologres import HologresConfig, HologresConnector
from datus_hologres.handlers import build_hologres_uri, resolve_hologres_context


def test_connector_initialization_with_config_object():
    config = HologresConfig(
        host="hologres.example.com",
        port=80,
        username="test_user",
        password="test_pass",
        database="testdb",
        schema_name="myschema",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = HologresConnector(config)

    assert connector.hologres_config == config
    assert connector.config == config
    assert connector.host == "hologres.example.com"
    assert connector.port == 80
    assert connector.username == "test_user"
    assert connector.password == "test_pass"
    assert connector.database_name == "testdb"
    assert connector.schema_name == "myschema"
    assert connector.db_type == "hologres"
    assert connector.adapter_type == "hologres"


def test_connector_initialization_with_dict():
    config_dict = {
        "host": "192.168.1.100",
        "port": 5433,
        "username": "admin",
        "password": "secret",
        "database": "mydb",
        "schema": "custom_schema",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = HologresConnector(config_dict)

    assert isinstance(connector.config, HologresConfig)
    assert connector.host == "192.168.1.100"
    assert connector.port == 5433
    assert connector.username == "admin"
    assert connector.password == "secret"
    assert connector.database_name == "mydb"
    assert connector.schema_name == "custom_schema"


def test_connector_initialization_invalid_type():
    with pytest.raises(TypeError, match="config must be HologresConfig or dict"):
        HologresConnector("invalid_config")


def test_connector_uses_postgresql_protocol_connection_string():
    config = HologresConfig(
        host="hologres.example.com",
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        HologresConnector(config)

    connection_string = mock_init.call_args[0][0]
    assert "postgresql+psycopg2://user:pass@hologres.example.com:80/db" in connection_string
    assert "sslmode=prefer" in connection_string
    assert mock_init.call_args.kwargs["dialect"] == "postgresql"


def test_connector_migration_capability_hints_are_lightweight():
    connector = HologresConnector.__new__(HologresConnector)

    capabilities = connector.describe_migration_capabilities()

    assert capabilities["supported"] is True
    assert capabilities["dialect_family"] == "postgres-like"
    assert capabilities["requires"] == []
    assert any("Hologres" in note for note in capabilities["notes"])
    assert "external tables" in capabilities["type_hints"]


def test_build_hologres_uri_uses_postgresql_driver():
    config = HologresConfig(
        host="hologres.example.com",
        username="user",
        password="pass",
        database="db",
        sslmode="require",
    )

    uri = build_hologres_uri(config)

    assert uri.startswith("postgresql+psycopg2://")
    assert "hologres.example.com:80/db" in uri
    assert "sslmode=require" in uri


def test_resolve_hologres_context_preserves_postgresql_fields():
    config = HologresConfig(username="user", database="fallback_db", schema="fallback_schema")
    uri = "postgresql+psycopg2://user:pass@hologres.example.com:80/db?options=-csearch_path%3Danalytics"

    adapter, catalog, database, schema = resolve_hologres_context(config, uri)

    assert adapter == "hologres"
    assert catalog == ""
    assert database == "db"
    assert schema == "analytics"
