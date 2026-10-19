# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest
from pandas import DataFrame
from sqlalchemy.engine import make_url

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector
from datus_db_core import DatusDbException


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with ClickHouseConfig object."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="test_user",
        password="test_pass",
        database="testdb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)

        assert connector.config == config
        assert connector.host == "localhost"
        assert connector.port == 8123
        assert connector.username == "test_user"
        assert connector.config.password == "test_pass"
        assert connector.database_name == "testdb"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 8123,
        "username": "admin",
        "password": "secret",
        "database": "mydb",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config_dict)

        assert connector.host == "192.168.1.100"
        assert connector.port == 8123
        assert connector.username == "admin"
        assert connector.config.password == "secret"
        assert connector.database_name == "mydb"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be ClickHouseConfig or dict"):
        ClickHouseConnector("invalid_config")


@pytest.mark.acceptance
def test_connector_connection_string_basic():
    """Test connection string generation with basic config."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        ClickHouseConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert make_url(connection_string).username == "user"
        assert make_url(connection_string).database == "db"


@pytest.mark.acceptance
def test_connector_connection_string_special_password():
    """Test connection string generation with special characters in password."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="p@ss!w0rd#$%",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        ClickHouseConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        # Password should be URL encoded
        assert make_url(connection_string).password == config.password


def test_connector_connection_string_no_password():
    """Test connection string generation with empty password."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        ClickHouseConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert make_url(connection_string).password == ""


def test_connector_connection_string_no_database():
    """Test connection string generation without database."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        ClickHouseConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert make_url(connection_string).database in (None, "")


@pytest.mark.acceptance
def test_sys_databases():
    """Test _sys_databases returns correct system databases."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        sys_dbs = connector._sys_databases()

        assert sys_dbs == {"INFORMATION_SCHEMA", "information_schema", "system"}
        assert isinstance(sys_dbs, set)


def test_sys_schemas():
    """Test _sys_schemas returns same as _sys_databases."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        sys_schemas = connector._sys_schemas()
        sys_dbs = connector._sys_databases()

        assert sys_schemas == sys_dbs


@pytest.mark.acceptance
def test_quote_identifier_basic():
    """Test _quote_identifier with basic identifier."""
    assert ClickHouseConnector.quote_identifier(MagicMock(), "table_name") == "`table_name`"


@pytest.mark.acceptance
def test_quote_identifier_with_backticks():
    """Test _quote_identifier escapes backticks."""
    assert ClickHouseConnector.quote_identifier(MagicMock(), "table`name") == "`table``name`"


def test_quote_identifier_with_multiple_backticks():
    """Test _quote_identifier escapes multiple backticks."""
    assert ClickHouseConnector.quote_identifier(MagicMock(), "ta`ble`name") == "`ta``ble``name`"


def test_quote_identifier_empty_string():
    """Test _quote_identifier with empty string."""
    assert ClickHouseConnector.quote_identifier(MagicMock(), "") == "``"


def test_quote_identifier_special_characters():
    """Test _quote_identifier with special characters."""
    assert ClickHouseConnector.quote_identifier(MagicMock(), "table-name_123") == "`table-name_123`"


@pytest.mark.acceptance
def test_full_name_with_database():
    """Test full_name method with database."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        full_name = connector.full_name(database_name="mydb", table_name="mytable")

        assert full_name == "`mydb`.`mytable`"


def test_full_name_without_database():
    """Test full_name method without database."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        full_name = connector.full_name(table_name="mytable")

        assert full_name == "`mytable`"


def test_full_name_with_special_characters():
    """Test full_name with special characters (backticks are escaped)."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        full_name = connector.full_name(database_name="my`db", table_name="my`table")

        # full_name escapes backticks by doubling them
        assert full_name == "`my``db`.`my``table`"


def test_identifier_with_database():
    """Test identifier method with database (inherited from base class)."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        with patch("datus_sqlalchemy.SQLAlchemyConnector.identifier") as mock_identifier:
            mock_identifier.return_value = "mydb.mytable"
            connector = ClickHouseConnector(config)
            identifier = connector.identifier(database_name="mydb", table_name="mytable")

            assert identifier == "mydb.mytable"


def test_identifier_without_database():
    """Test identifier method without database (inherited from base class)."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        with patch("datus_sqlalchemy.SQLAlchemyConnector.identifier") as mock_identifier:
            mock_identifier.return_value = "mytable"
            connector = ClickHouseConnector(config)
            identifier = connector.identifier(table_name="mytable")

        assert identifier == "mytable"


def test_show_create_reads_statement_column():
    """Test _show_create returns ClickHouse DDL from single statement column."""
    config = ClickHouseConfig(username="user")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)
        ddl_frame = DataFrame({"statement": ["CREATE TABLE mydb.mytable (`id` Int64) ENGINE = MergeTree()"]})

        with patch.object(connector, "_execute_pandas", return_value=ddl_frame):
            ddl = connector._show_create("`mydb`.`mytable`", "TABLE")

        assert ddl.startswith("CREATE TABLE mydb.mytable")


@pytest.mark.acceptance
def test_get_metadata_config_valid_table_type():
    """Test _get_metadata_config with valid table type."""
    from datus_clickhouse.connector import _get_metadata_config

    config = _get_metadata_config("table")
    assert config.show_table == "TABLES"
    assert config.show_create_table == "TABLE"
    assert config.info_table == "TABLES"
    assert config.table_types == ["BASE TABLE"]


def test_get_metadata_config_view_type():
    """Test _get_metadata_config with view type."""
    from datus_clickhouse.connector import _get_metadata_config

    config = _get_metadata_config("view")
    assert config.show_table == "VIEWS"
    assert config.show_create_table == "VIEW"
    assert config.info_table == "VIEWS"


@pytest.mark.acceptance
def test_get_metadata_config_invalid_type():
    """Test _get_metadata_config with invalid table type."""
    from datus_clickhouse.connector import _get_metadata_config

    with pytest.raises(DatusDbException, match="Invalid table type"):
        _get_metadata_config("invalid_type")


def test_connector_stores_config():
    """Test that connector stores the config object."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)

        assert connector.config == config
        assert isinstance(connector.config, ClickHouseConfig)


def test_connector_database_name_attribute():
    """Test that connector sets database_name attribute."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="pass",
        database="testdb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)

        assert connector.database_name == "testdb"


def test_connector_database_name_empty_when_none():
    """Test that database_name is empty string when config.database is None."""
    config = ClickHouseConfig(
        host="localhost",
        port=8123,
        username="user",
        password="pass",
        database=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = ClickHouseConnector(config)

        assert connector.database_name == ""
