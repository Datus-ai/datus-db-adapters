# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from collections import namedtuple
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from datus_db_core.exceptions import DatusDbException
from datus_hive import HiveConfig
from datus_hive.connector import HiveConnector


@pytest.fixture
def connector():
    return HiveConnector(
        HiveConfig(
            host="localhost",
            port=10000,
            database="default",
            username="hue",
            password="pass",
            auth="CUSTOM",
            configuration={
                "spark.app.name": "datacenter_carrier",
                "spark.sql.shuffle.partitions": 100,
            },
        )
    )


# ==================== Initialization Tests ====================


def test_connector_initialization_with_config_object():
    """Test connector initialization with HiveConfig object."""
    config = HiveConfig(
        host="10.0.0.1",
        port=10009,
        username="hive_user",
        password="secret",
        database="mydb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)

        assert conn.config == config
        assert conn.host == "10.0.0.1"
        assert conn.port == 10009
        assert conn.username == "hive_user"
        assert conn.database_name == "mydb"


def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 10009,
        "username": "admin",
        "password": "secret",
        "database": "testdb",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config_dict)

        assert conn.host == "192.168.1.100"
        assert conn.port == 10009
        assert conn.username == "admin"
        assert conn.database_name == "testdb"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be HiveConfig or dict"):
        HiveConnector("invalid_config")


def test_connector_database_name_defaults_to_default():
    """Test that database_name defaults to 'default' when config.database is None."""
    config = HiveConfig(username="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)

        assert conn.database_name == "default"


def test_connector_connection_string_basic():
    """Test connection string generation with basic config."""
    config = HiveConfig(
        host="localhost",
        port=10000,
        username="hive_user",
        database="mydb",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        HiveConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "hive://hive_user@localhost:10000/mydb" in connection_string


def test_connector_connection_string_special_username():
    """Test connection string generation with special characters in username."""
    config = HiveConfig(
        host="localhost",
        port=10000,
        username="user@domain",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        HiveConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        # Username should be URL encoded
        assert "user%40domain" in connection_string


def test_connector_stores_config():
    """Test that connector stores the config object after super().__init__()."""
    config = HiveConfig(
        host="localhost",
        port=10000,
        username="hive",
        database="db",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)

        assert conn.config == config
        assert isinstance(conn.config, HiveConfig)


# ==================== _build_connect_args Tests ====================


def test_build_connect_args_normalizes_configuration(connector):
    connect_args = connector._build_connect_args(connector.config)

    assert connect_args["configuration"]["spark.sql.shuffle.partitions"] == "100"


def test_build_connect_args_with_auth():
    """Test _build_connect_args includes auth when set."""
    config = HiveConfig(username="hive", auth="CUSTOM")
    args = HiveConnector._build_connect_args(config)

    assert args["auth"] == "CUSTOM"


def test_build_connect_args_with_password():
    """Test _build_connect_args includes password when set."""
    config = HiveConfig(username="hive", password="secret")
    args = HiveConnector._build_connect_args(config)

    assert args["password"] == "secret"


def test_build_connect_args_minimal():
    """Test _build_connect_args with minimal config (no auth, no password, no configuration)."""
    config = HiveConfig(username="hive")
    args = HiveConnector._build_connect_args(config)

    assert args == {}


@pytest.mark.parametrize(
    "service_name, expected_service",
    [("hive", "hive"), ("custom_hive", "custom_hive"), (" \thive\n", "hive"), ("Custom_Hive", "Custom_Hive")],
)
def test_kerberos_arguments_reach_engine(service_name, expected_service):
    connection = HiveConnector(HiveConfig(username="alice", auth="KERBEROS", kerberos_service_name=service_name))
    with (
        patch("datus_hive.connector.create_engine") as factory,
        patch("datus_hive.connector.configure_thrift_timeouts"),
    ):
        engine = connection._ensure_engine()
        assert engine is factory.return_value
        assert factory.call_args.kwargs["connect_args"] == {
            "auth": "KERBEROS",
            "kerberos_service_name": expected_service,
        }
    connection.close()


def test_kerberos_arguments_reach_pyhive():
    connection = HiveConnector(
        HiveConfig(host="hs2.example.com", username="alice", auth="KERBEROS", kerberos_service_name="hive")
    )
    try:
        with (
            patch("pyhive.hive.Connection", side_effect=RuntimeError("driver boundary")) as driver,
            patch("pyhive.hive.get_installed_sasl") as sasl,
        ):
            with pytest.raises(RuntimeError, match="driver boundary"):
                connection._ensure_engine().connect()
            args = driver.call_args.kwargs
            assert args["username"] == "alice"
            assert args["database"] == "default"
            transport = args["thrift_transport"]
            assert transport.mechanism == "GSSAPI"
            assert transport._trans.host == "hs2.example.com"
            assert transport._trans._timeout == 30
            transport.sasl_client_factory()
            sasl.assert_called_once_with(
                host="hs2.example.com", sasl_auth="GSSAPI", service="hive", username="alice", password=None
            )
    finally:
        connection.close()


@pytest.mark.parametrize("auth", [None, "NONE", "LDAP", "CUSTOM"])
def test_non_kerberos_arguments(auth):
    password = "secret" if auth in ("LDAP", "CUSTOM") else ""
    config = HiveConfig(username="hive", auth=auth, password=password)
    expected = {"auth": auth} if auth else {}
    if password:
        expected["password"] = password
    assert HiveConnector._build_connect_args(config) == expected


# ==================== _normalize_configuration Tests ====================


def test_normalize_configuration_bool_values():
    """Test _normalize_configuration converts booleans to strings."""
    result = HiveConnector._normalize_configuration({"flag_true": True, "flag_false": False})

    assert result == {"flag_true": "true", "flag_false": "false"}


def test_normalize_configuration_none_values():
    """Test _normalize_configuration converts None to empty string."""
    result = HiveConnector._normalize_configuration({"key": None})

    assert result == {"key": ""}


def test_normalize_configuration_numeric_values():
    """Test _normalize_configuration converts numbers to strings."""
    result = HiveConnector._normalize_configuration({"count": 100, "ratio": 0.5})

    assert result == {"count": "100", "ratio": "0.5"}


def test_normalize_configuration_string_values():
    """Test _normalize_configuration keeps strings as-is."""
    result = HiveConnector._normalize_configuration({"name": "app"})

    assert result == {"name": "app"}


# ==================== _quote_identifier Tests ====================


def test_quote_identifier_basic():
    """Test _quote_identifier with basic identifier."""
    assert HiveConnector.quote_identifier(MagicMock(), "table_name") == "`table_name`"


def test_quote_identifier_with_backticks():
    """Test _quote_identifier escapes backticks."""
    assert HiveConnector.quote_identifier(MagicMock(), "table`name") == "`table``name`"


def test_quote_identifier_empty_string():
    """Test _quote_identifier with empty string."""
    assert HiveConnector.quote_identifier(MagicMock(), "") == "``"


def test_quote_identifier_special_characters():
    """Test _quote_identifier with special characters."""
    assert HiveConnector.quote_identifier(MagicMock(), "table-name_123") == "`table-name_123`"


# ==================== full_name Tests ====================


def test_full_name_with_database():
    """Test full_name method with database."""
    config = HiveConfig(username="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)
        result = conn.full_name(database_name="mydb", table_name="mytable")

        assert result == "`mydb`.`mytable`"


def test_full_name_without_database():
    """Test full_name method without database falls back to self.database_name."""
    config = HiveConfig(username="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)
        conn.database_name = ""
        result = conn.full_name(table_name="mytable")

        assert result == "`mytable`"


def test_full_name_with_special_characters():
    """Test full_name with backticks in names."""
    config = HiveConfig(username="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)
        result = conn.full_name(database_name="my`db", table_name="my`table")

        assert result == "`my``db`.`my``table`"


# ==================== _sys_databases Tests ====================


def test_sys_databases():
    """Test _sys_databases returns correct system databases."""
    config = HiveConfig(username="hive")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        conn = HiveConnector(config)
        sys_dbs = conn._sys_databases()

        assert sys_dbs == {"information_schema", "sys"}
        assert isinstance(sys_dbs, set)


# ==================== get_databases Tests ====================


def test_get_databases_parses_results(monkeypatch, connector):
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame({"database_name": ["default", "test"]}),
    )

    assert connector.get_databases() == ["default", "test"]


def test_get_databases_filters_system_dbs(monkeypatch, connector):
    """Test that get_databases filters out system databases."""
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame({"database_name": ["default", "information_schema", "sys", "mydb"]}),
    )

    result = connector.get_databases()
    assert result == ["default", "mydb"]
    assert "information_schema" not in result
    assert "sys" not in result


def test_get_databases_include_sys(monkeypatch, connector):
    """Test that get_databases includes system databases when include_sys=True."""
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame({"database_name": ["default", "information_schema", "sys"]}),
    )

    result = connector.get_databases(include_sys=True)
    assert "information_schema" in result
    assert "sys" in result


def test_get_databases_empty(monkeypatch, connector):
    """Test get_databases returns empty list when no databases."""
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(connector, "_execute_pandas", lambda sql: pd.DataFrame())

    assert connector.get_databases() == []


# ==================== get_tables / get_views Tests ====================


def test_get_tables_parses_results(monkeypatch, connector):
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: (
            pd.DataFrame({"database": ["default"], "tableName": ["table_a"], "isTemporary": [False]})
            if sql.startswith("SHOW TABLES")
            else pd.DataFrame()
        ),
    )

    assert connector.get_tables(database_name="default") == ["table_a"]


def test_get_views_parses_results(monkeypatch, connector):
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame({"database": ["default"], "viewName": ["view_a"], "isTemporary": [False]}),
    )

    assert connector.get_views(database_name="default") == ["view_a"]


@pytest.mark.parametrize("method", ["get_views", "get_tables", "get_views_with_ddl", "get_tables_with_ddl"])
@pytest.mark.parametrize("message", ["SHOW VIEWS not supported", "Permission denied for SHOW VIEWS"])
def test_metadata_discovery_preserves_view_lookup_errors(connector, method, message):
    """A failed view inventory must never become a successful table/view listing."""
    error = RuntimeError(message)
    row = namedtuple("ListedObject", "tab_name")
    connection = MagicMock()
    connector.engine = MagicMock()
    connector._owns_engine = True
    connector.engine.connect.return_value = connection

    def execute(statement, **kwargs):
        sql = str(statement)
        result = MagicMock()
        if sql.startswith("SHOW VIEWS"):
            raise error
        if sql.startswith("SHOW TABLES"):
            result.fetchall.return_value = [row("base_table"), row("ordinary_view")]
        return result

    connection.exec_driver_sql.side_effect = execute
    with pytest.raises(DatusDbException, match=message) as raised:
        getattr(connector, method)(database_name="default")
    assert raised.value.__cause__ is error
    queries = [str(call.args[0]) for call in connection.exec_driver_sql.call_args_list]
    assert "SHOW VIEWS IN `default`" in queries
    assert not any(sql.startswith("SHOW CREATE") for sql in queries)
    connector.close()


@pytest.mark.parametrize("method, expected", [("get_views", []), ("get_tables", ["base_table"])])
def test_successful_empty_view_inventory(connector, method, expected):
    """An actual empty view result remains a valid, successful inventory."""
    row = namedtuple("ListedObject", "tab_name")
    connection = MagicMock()
    connector.engine = MagicMock()
    connector._owns_engine = True
    connector.engine.connect.return_value = connection

    def execute(statement, **kwargs):
        sql = str(statement)
        result = MagicMock()
        result.fetchall.return_value = [row("base_table")] if sql.startswith("SHOW TABLES") else []
        return result

    connection.exec_driver_sql.side_effect = execute
    assert getattr(connector, method)(database_name="default") == expected
    connector.close()


# ==================== get_schema Tests ====================


def test_get_schema_ignores_partition_section(monkeypatch, connector):
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame(
            {
                "col_name": ["id", "name", "# Partition Information", "dt"],
                "data_type": ["int", "string", "", "string"],
                "comment": ["", "", "", ""],
            }
        ),
    )

    schema = connector.get_schema(database_name="default", table_name="table_a")
    assert [col["name"] for col in schema] == ["id", "name"]


def test_get_schema_handles_none_type_and_comment(monkeypatch, connector):
    """Test that get_schema handles None values in type and comment columns."""
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "_execute_pandas",
        lambda sql: pd.DataFrame(
            {
                "col_name": ["id", "name"],
                "data_type": ["int", None],
                "comment": [None, None],
            }
        ),
    )

    schema = connector.get_schema(database_name="default", table_name="table_a")
    assert len(schema) == 2
    assert schema[0]["type"] == "int"
    assert schema[0]["comment"] is None  # None, not "None"
    assert schema[1]["type"] == ""  # empty string, not "None"
    assert schema[1]["comment"] is None


def test_get_schema_returns_empty_for_no_table_name(connector):
    """Test that get_schema returns empty list when no table_name."""
    assert connector.get_schema() == []


# ==================== get_tables_with_ddl Tests ====================


def test_get_tables_with_ddl(monkeypatch, connector):
    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(connector, "get_tables", lambda **kwargs: ["table_a"])
    monkeypatch.setattr(connector, "_show_create", lambda full_name: "CREATE TABLE table_a (id INT)")

    ddl_list = connector.get_tables_with_ddl(database_name="default")
    assert ddl_list
    assert ddl_list[0]["table_name"] == "table_a"
    assert "CREATE TABLE" in ddl_list[0]["definition"]


# ==================== get_sample_rows Tests ====================


def test_get_sample_rows_continues_on_error(monkeypatch, connector):
    """Test that get_sample_rows skips failed tables and continues."""
    call_count = {"n": 0}

    def mock_execute_pandas(sql):
        call_count["n"] += 1
        if "bad_table" in sql:
            raise Exception("Permission denied")
        return pd.DataFrame({"id": [1], "name": ["test"]})

    monkeypatch.setattr(connector, "connect", lambda: None)
    monkeypatch.setattr(
        connector,
        "get_tables",
        lambda **kwargs: ["good_table", "bad_table", "another_good"],
    )
    monkeypatch.setattr(connector, "_execute_pandas", mock_execute_pandas)

    result = connector.get_sample_rows(database_name="default")
    assert len(result) == 2  # good_table + another_good, bad_table skipped
    assert call_count["n"] == 3  # all 3 tables were attempted


# ==================== _extract_table_names Tests ====================


def test_extract_table_names_with_tab_name_column():
    """Test _extract_table_names recognizes tab_name column."""
    df = pd.DataFrame({"tab_name": ["t1", "t2"]})
    assert HiveConnector._extract_table_names(df) == ["t1", "t2"]


def test_extract_table_names_with_table_name_column():
    """Test _extract_table_names recognizes table_name column."""
    df = pd.DataFrame({"table_name": ["t1", "t2"]})
    assert HiveConnector._extract_table_names(df) == ["t1", "t2"]


def test_extract_table_names_empty_dataframe():
    """Test _extract_table_names returns empty list for empty DataFrame."""
    assert HiveConnector._extract_table_names(pd.DataFrame()) == []


def test_extract_table_names_fallback_to_string_column():
    """Test _extract_table_names falls back to first string column."""
    df = pd.DataFrame({"unknown_col": ["t1", "t2"], "num_col": [1, 2]})
    assert HiveConnector._extract_table_names(df) == ["t1", "t2"]
