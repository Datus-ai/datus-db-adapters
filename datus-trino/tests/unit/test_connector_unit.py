# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest

from datus_db_core import CatalogSupportMixin
from datus_trino import TrinoConfig, TrinoConnector

# ==================== Initialization Tests ====================


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with TrinoConfig object."""
    config = TrinoConfig(
        host="localhost",
        port=8080,
        username="test_user",
        password="test_pass",
        catalog="hive",
        schema_name="myschema",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert connector.trino_config == config
        assert connector.catalog_name == "hive"
        assert connector.schema_name == "myschema"
        assert connector.dialect == "trino"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 443,
        "username": "admin",
        "password": "secret",
        "catalog": "iceberg",
        "schema_name": "analytics",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config_dict)

        assert isinstance(connector.trino_config, TrinoConfig)
        assert connector.catalog_name == "iceberg"
        assert connector.schema_name == "analytics"
        assert connector.dialect == "trino"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be TrinoConfig or dict"):
        TrinoConnector("invalid_config")


def test_connector_stores_trino_config():
    """Test that connector stores TrinoConfig object."""
    config = TrinoConfig(username="test_user", catalog="my_catalog")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert hasattr(connector, "trino_config")
        assert connector.trino_config.catalog == "my_catalog"


def test_connector_passes_connection_string_to_parent():
    """Test that connector builds and passes connection string to parent."""
    config = TrinoConfig(
        host="localhost",
        port=8080,
        username="user",
        password="pass",
        catalog="hive",
        schema_name="default",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        TrinoConnector(config)

        mock_init.assert_called_once()
        conn_string = mock_init.call_args[0][0]
        assert "trino://" in conn_string
        assert "localhost" in conn_string
        assert "8080" in conn_string
        assert "http_scheme=http" in conn_string


def test_connector_connection_string_with_https():
    """Test connection string includes https scheme."""
    config = TrinoConfig(
        host="localhost",
        port=443,
        username="user",
        http_scheme="https",
        verify=False,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        connector = TrinoConnector(config)

        conn_string = mock_init.call_args[0][0]
        assert "http_scheme=https" in conn_string
        assert connector._verify_ssl is False


def test_connector_verify_ssl_default_true():
    """Test _verify_ssl defaults to True."""
    config = TrinoConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert connector._verify_ssl is True


def test_connector_connection_string_without_password():
    """Test connection string when password is empty."""
    config = TrinoConfig(
        host="localhost",
        port=8080,
        username="user",
        catalog="hive",
        schema_name="default",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        TrinoConnector(config)

        conn_string = mock_init.call_args[0][0]
        assert "user@" in conn_string
        # No password portion
        assert ":@" not in conn_string or "user:@" not in conn_string


# ==================== _ensure_engine() Tests ====================


def test_ensure_engine_forwards_http_scheme_and_verify():
    """Regression: the Trino SQLAlchemy dialect ignores ?http_scheme=... in the URL,
    so _ensure_engine() must forward the scheme via connect_args (see PR #80)."""
    config = TrinoConfig(
        host="localhost",
        port=8443,  # non-standard TLS port: 443 may infer HTTPS and mask a broken impl
        username="user",
        http_scheme="https",
        verify=False,
    )
    connector = TrinoConnector(config)

    with patch("datus_trino.connector.create_engine") as mock_create_engine:
        engine = connector._ensure_engine()

        mock_create_engine.assert_called_once()
        connect_args = mock_create_engine.call_args.kwargs["connect_args"]
        assert connect_args == {"verify": False, "http_scheme": "https"}
        assert engine is mock_create_engine.return_value
        assert connector._owns_engine is True


def test_ensure_engine_default_http_scheme():
    """Default config (http, verify=True) is forwarded through connect_args unchanged."""
    config = TrinoConfig(host="localhost", port=8080, username="user")
    connector = TrinoConnector(config)

    with patch("datus_trino.connector.create_engine") as mock_create_engine:
        connector._ensure_engine()

        connect_args = mock_create_engine.call_args.kwargs["connect_args"]
        assert connect_args == {"verify": True, "http_scheme": "http"}


# ==================== Catalog Functionality Unit Tests ====================


@pytest.mark.acceptance
def test_default_catalog_returns_configured_catalog():
    """Test that default_catalog returns configured catalog."""
    config = TrinoConfig(username="test_user", catalog="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert connector.default_catalog() == "hive"


def test_default_catalog_custom():
    """Test default_catalog with custom catalog."""
    config = TrinoConfig(username="test_user", catalog="iceberg")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert connector.default_catalog() == "iceberg"


@pytest.mark.acceptance
def test_switch_catalog_updates_catalog_name():
    """Test that switch_catalog updates catalog_name attribute."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        connector.switch_catalog("new_catalog")

        assert connector.catalog_name == "new_catalog"


# ==================== full_name() Method Tests ====================


@pytest.mark.acceptance
def test_full_name_with_catalog_schema_table():
    """Test full_name with catalog, schema, and table."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.full_name(catalog_name="my_catalog", schema_name="my_schema", table_name="my_table")

        assert result == '"my_catalog"."my_schema"."my_table"'


def test_full_name_with_database_name():
    """Test full_name with database_name (maps to schema)."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.full_name(catalog_name="cat", database_name="db", table_name="tbl")

        assert result == '"cat"."db"."tbl"'


def test_full_name_uses_default_catalog():
    """Test full_name uses default catalog when not specified."""
    config = TrinoConfig(username="test_user", catalog="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.full_name(schema_name="my_schema", table_name="my_table")

        assert result == '"hive"."my_schema"."my_table"'


def test_full_name_table_only():
    """Test full_name with table only (no schema)."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)
        connector.catalog_name = ""
        connector.schema_name = ""

        result = connector.full_name(table_name="my_table")

        assert result == '"my_table"'


@pytest.mark.acceptance
def test_full_name_uses_double_quotes():
    """Test full_name uses double quotes for identifiers."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.full_name(catalog_name="catalog", schema_name="schema", table_name="table")

        assert result.count('"') == 6  # 3 pairs of double quotes


def test_full_name_with_special_characters():
    """Test full_name with special characters in names."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.full_name(
            catalog_name="test-catalog",
            schema_name="test_schema",
            table_name="test-table",
        )

        assert '"test-catalog"' in result
        assert '"test_schema"' in result
        assert '"test-table"' in result


# ==================== _sqlalchemy_schema() Tests ====================


def test_sqlalchemy_schema_with_schema_name():
    """Test _sqlalchemy_schema returns schema name."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector._sqlalchemy_schema(schema_name="test_schema")

        assert result == "test_schema"


def test_sqlalchemy_schema_with_database_name():
    """Test _sqlalchemy_schema falls back to database_name."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector._sqlalchemy_schema(database_name="test_db")

        assert result == "test_db"


def test_sqlalchemy_schema_uses_default():
    """Test _sqlalchemy_schema uses default schema_name."""
    config = TrinoConfig(username="test_user", schema_name="default")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector._sqlalchemy_schema()

        assert result == "default"


# ==================== Utility Method Tests ====================


def test_to_dict_includes_catalog():
    """Test to_dict includes catalog field."""
    config = TrinoConfig(username="test_user", catalog="my_catalog")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        result = connector.to_dict()

        assert result["db_type"] == "trino"
        assert result["catalog"] == "my_catalog"
        assert result["host"] == "127.0.0.1"
        assert result["port"] == 8080


def test_get_type_returns_trino():
    """Test get_type returns trino."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert connector.get_type() == "trino"


def test_context_manager_support():
    """Test connector supports context manager protocol."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)
        connector.connect = MagicMock()
        connector.close = MagicMock()

        with connector as conn:
            assert conn is connector
            connector.connect.assert_called_once()

        connector.close.assert_called_once()


# ==================== Mixin Interface Tests ====================


@pytest.mark.acceptance
def test_implements_catalog_support_mixin():
    """Test connector implements CatalogSupportMixin."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        assert isinstance(connector, CatalogSupportMixin)


# ==================== System Database/Schema Tests ====================


def test_sys_databases():
    """Test system databases list."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        sys_dbs = connector._sys_databases()
        assert "information_schema" in sys_dbs


def test_sys_schemas():
    """Test system schemas list."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        sys_schemas = connector._sys_schemas()
        assert "information_schema" in sys_schemas


# ==================== do_switch_context Tests ====================


def test_switch_context_catalog():
    """Test switch_context updates catalog."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        connector.switch_context(catalog_name="new_catalog")

        assert connector.catalog_name == "new_catalog"


def test_switch_context_schema():
    """Test switch_context updates schema."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        connector.switch_context(schema_name="new_schema")

        assert connector.schema_name == "new_schema"


def test_switch_context_database():
    """Test switch_context with database_name updates database."""
    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)

        connector.switch_context(database_name="new_db")

        assert connector.database_name == "new_db"


def test_thread_local_context_isolation():
    """Two threads switching context see their own catalog/schema values."""
    import threading
    import time

    config = TrinoConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = TrinoConnector(config)
        results = {}

        def worker(thread_id, catalog, schema):
            connector.switch_context(catalog_name=catalog, schema_name=schema)
            time.sleep(0.05)
            results[thread_id] = {
                "catalog": connector.catalog_name,
                "schema": connector.schema_name,
            }

        t1 = threading.Thread(target=worker, args=(1, "hive", "analytics"))
        t2 = threading.Thread(target=worker, args=(2, "iceberg", "raw"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results[1] == {"catalog": "hive", "schema": "analytics"}
        assert results[2] == {"catalog": "iceberg", "schema": "raw"}


# ==================== Quote Identifier Tests ====================


def test_quote_identifier():
    """Test _quote_identifier uses double quotes."""
    assert TrinoConnector.quote_identifier(MagicMock(), "table") == '"table"'


def test_quote_identifier_with_special_chars():
    """Test _quote_identifier escapes double quotes."""
    assert TrinoConnector.quote_identifier(MagicMock(), 'my"table') == '"my""table"'
