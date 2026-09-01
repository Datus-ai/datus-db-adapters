# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest

from datus_spark import SparkConfig, SparkConnector

# ==================== Initialization Tests ====================


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with SparkConfig object."""
    config = SparkConfig(
        host="localhost",
        port=10000,
        username="test_user",
        password="test_pass",
        database="testdb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        assert connector.spark_config == config
        assert connector.database_name == "testdb"
        assert connector.dialect == "spark"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 10001,
        "username": "admin",
        "password": "secret",
        "database": "mydb",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config_dict)

        assert isinstance(connector.spark_config, SparkConfig)
        assert connector.database_name == "mydb"
        assert connector.dialect == "spark"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be SparkConfig or dict"):
        SparkConnector("invalid_config")


def test_connector_stores_spark_config():
    """Test that connector stores SparkConfig object."""
    config = SparkConfig(username="test_user", database="my_db")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        assert hasattr(connector, "spark_config")
        assert connector.spark_config.database == "my_db"


def test_connector_passes_connection_string_to_parent():
    """Test that connector builds and passes connection string to parent."""
    config = SparkConfig(
        host="localhost",
        port=10000,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        SparkConnector(config)

        mock_init.assert_called_once()
        conn_string = mock_init.call_args[0][0]
        assert "hive://" in conn_string
        assert "localhost" in conn_string
        assert "10000" in conn_string


def test_connector_default_database():
    """Test connector uses 'default' when database is None."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        assert connector.database_name == "default"


def test_connector_connection_string_with_custom_auth():
    """Test CUSTOM authentication is passed through to PyHive."""
    config = SparkConfig(
        host="localhost",
        port=10000,
        username="user",
        password="pass",
        auth_mechanism="CUSTOM",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        SparkConnector(config)

        conn_string = mock_init.call_args[0][0]
        assert conn_string == "hive://user:pass@localhost:10000/default?auth=CUSTOM"


def test_connector_connection_string_without_auth():
    """Test connection string without auth mechanism (NONE)."""
    config = SparkConfig(
        host="localhost",
        port=10000,
        username="user",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        SparkConnector(config)

        conn_string = mock_init.call_args[0][0]
        assert "auth=" not in conn_string


# ==================== full_name() Method Tests ====================


@pytest.mark.acceptance
def test_full_name_with_database_and_table():
    """Test full_name with database and table."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.full_name(database_name="my_db", table_name="my_table")

        assert result == "`my_db`.`my_table`"


def test_full_name_table_only():
    """Test full_name with table only."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)
        connector.database_name = ""

        result = connector.full_name(table_name="my_table")

        assert result == "`my_table`"


def test_full_name_uses_default_database():
    """Test full_name uses default database."""
    config = SparkConfig(username="test_user", database="testdb")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.full_name(table_name="my_table")

        assert result == "`testdb`.`my_table`"


@pytest.mark.acceptance
def test_full_name_uses_backticks():
    """Test full_name uses backticks for identifiers."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.full_name(database_name="database", table_name="table")

        assert result.count("`") == 4  # 2 pairs of backticks


def test_full_name_with_special_characters():
    """Test full_name with special characters in names."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.full_name(database_name="test-db", table_name="test-table")

        assert "`test-db`" in result
        assert "`test-table`" in result


# ==================== _sqlalchemy_schema() Tests ====================


def test_sqlalchemy_schema_with_database_name():
    """Test _sqlalchemy_schema returns database name."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector._sqlalchemy_schema(database_name="test_db")

        assert result == "test_db"


def test_sqlalchemy_schema_uses_default():
    """Test _sqlalchemy_schema uses default database."""
    config = SparkConfig(username="test_user", database="mydb")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector._sqlalchemy_schema()

        assert result == "mydb"


# ==================== System Database Tests ====================


def test_sys_databases():
    """Test system databases list."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        sys_dbs = connector._sys_databases()
        assert "information_schema" in sys_dbs


def test_sys_schemas():
    """Test system schemas list."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        sys_schemas = connector._sys_schemas()
        assert "information_schema" in sys_schemas


# ==================== get_schemas Tests ====================


def test_get_schemas_returns_empty():
    """Test get_schemas returns empty list (Spark has no separate schemas)."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.get_schemas()
        assert result == []


# ==================== Utility Method Tests ====================


def test_to_dict():
    """Test to_dict includes all fields."""
    config = SparkConfig(username="test_user", database="mydb")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        result = connector.to_dict()

        assert result["db_type"] == "spark"
        assert result["host"] == "127.0.0.1"
        assert result["port"] == 10000
        assert result["database"] == "mydb"


def test_get_type_returns_spark():
    """Test get_type returns spark."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)

        assert connector.get_type() == "spark"


def test_context_manager_support():
    """Test connector supports context manager protocol."""
    config = SparkConfig(username="test_user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)
        connector.connect = MagicMock()
        connector.close = MagicMock()

        with connector as conn:
            assert conn is connector
            connector.connect.assert_called_once()

        connector.close.assert_called_once()


# ==================== Quote Identifier Tests ====================


def test_do_switch_context_uses_persistent_connection():
    """do_switch_context executes USE on the given connection."""
    config = SparkConfig(username="test_user", database="db")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)
        mock_conn = MagicMock()

        connector.do_switch_context(mock_conn, database_name="new_db")

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


def test_do_switch_context_noop_without_database():
    """do_switch_context should do nothing when database_name is empty."""
    config = SparkConfig(username="test_user", database="db")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = SparkConnector(config)
        mock_conn = MagicMock()

        connector.do_switch_context(mock_conn)

        mock_conn.execute.assert_not_called()


# ==================== Quote Identifier Tests ====================


def test_quote_identifier():
    """Test _quote_identifier uses backticks."""
    assert SparkConnector.quote_identifier(MagicMock(), "table") == "`table`"


def test_quote_identifier_with_special_chars():
    """Test _quote_identifier escapes backticks."""
    assert SparkConnector.quote_identifier(MagicMock(), "my`table") == "`my``table`"
