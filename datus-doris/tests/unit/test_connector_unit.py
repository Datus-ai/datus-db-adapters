# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest

from datus_db_core import CatalogSupportMixin, MaterializedViewSupportMixin, SQLType
from datus_doris import DorisConfig, DorisConnector

# ==================== Initialization Tests ====================


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with DorisConfig object."""
    config = DorisConfig(
        host="localhost",
        port=9030,
        username="test_user",
        password="test_pass",
        catalog="test_catalog",
        database="testdb",
    )

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert connector.doris_config == config
        assert connector.catalog_name == "test_catalog"
        assert connector.dialect == "doris"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "host": "192.168.1.100",
        "port": 9031,
        "username": "admin",
        "password": "secret",
        "catalog": "custom_catalog",
        "database": "mydb",
    }

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config_dict)

        assert isinstance(connector.doris_config, DorisConfig)
        assert connector.catalog_name == "custom_catalog"
        assert connector.dialect == "doris"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be DorisConfig or dict"):
        DorisConnector("invalid_config")


def test_connector_stores_doris_config():
    """Test that connector stores DorisConfig object."""
    config = DorisConfig(username="test_user", catalog="my_catalog")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert hasattr(connector, "doris_config")
        assert connector.doris_config.catalog == "my_catalog"


def test_connector_passes_mysql_config_to_parent():
    """Test that connector converts and passes MySQLConfig to parent."""
    config = DorisConfig(
        host="localhost",
        port=9030,
        username="user",
        password="pass",
        database="db",
    )

    with patch("datus_mysql.MySQLConnector.__init__") as mock_init:
        DorisConnector(config)

        mock_init.assert_called_once()
        mysql_config = mock_init.call_args[0][0]
        assert mysql_config.host == "localhost"
        assert mysql_config.port == 9030
        assert mysql_config.username == "user"


# ==================== Catalog Functionality Unit Tests ====================


@pytest.mark.acceptance
def test_internal_returns_default_catalog():
    """Test that internal returns 'internal'."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert connector.default_catalog() == "internal"


def test_resolve_catalog_with_empty():
    """Test _resolve_catalog with empty string falls back to default."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.catalog_name = ""

        result = connector._resolve_catalog("")
        assert result == "internal"


def test_resolve_catalog_with_def():
    """Test _resolve_catalog with 'def' string returns default."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector._resolve_catalog("def")
        assert result == "internal"


def test_get_current_context_resolves_default_catalog():
    """Test current context exposes effective SQL coordinates."""
    config = DorisConfig(username="test_user", database="ac_manage")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert connector.get_current_context() == {
            "catalog_name": "internal",
            "database_name": "ac_manage",
            "schema_name": "",
        }


def test_get_current_context_normalizes_def_catalog():
    """Test current context normalizes Doris catalog aliases."""
    config = DorisConfig(username="test_user", catalog="def", database="ac_manage")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.catalog_name = "def"

        assert connector.get_current_context()["catalog_name"] == "internal"


def test_get_current_context_keeps_custom_catalog():
    """Test current context keeps configured non-default catalogs."""
    config = DorisConfig(username="test_user", catalog="external_catalog", database="analytics")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert connector.get_current_context() == {
            "catalog_name": "external_catalog",
            "database_name": "analytics",
            "schema_name": "",
        }


def test_resolve_catalog_preserves_custom():
    """Test _resolve_catalog preserves custom catalog."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector._resolve_catalog("my_catalog")
        assert result == "my_catalog"


@pytest.mark.acceptance
def test_switch_catalog_updates_catalog_name():
    """Test that switch_catalog updates catalog_name via switch_context."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        connector.switch_catalog("new_catalog")

        assert connector.catalog_name == "new_catalog"


def test_switch_catalog_calls_switch_context():
    """Test that switch_catalog calls switch_context."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.switch_context = MagicMock()

        connector.switch_catalog("target_catalog")

        connector.switch_context.assert_called_once_with(catalog_name="target_catalog")


def test_resolve_catalog_falls_back_to_connector_catalog():
    """Test _resolve_catalog uses connector's catalog_name when no arg given."""
    config = DorisConfig(username="test_user", catalog="configured_catalog")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector._resolve_catalog("")
        assert result == "configured_catalog"


def test_resolve_catalog_arg_takes_precedence():
    """Test _resolve_catalog uses explicit arg over connector catalog."""
    config = DorisConfig(username="test_user", catalog="configured_catalog")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector._resolve_catalog("explicit_catalog")
        assert result == "explicit_catalog"


# ==================== full_name() Method Tests ====================


@pytest.mark.acceptance
def test_full_name_with_catalog_and_database():
    """Test full_name with catalog, database, and table."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(catalog_name="my_catalog", database_name="my_db", table_name="my_table")

        assert result == "`my_catalog`.`my_db`.`my_table`"


def test_full_name_with_catalog_only():
    """Test full_name with catalog and table only."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(catalog_name="my_catalog", table_name="my_table")

        assert result == "`my_table`"


def test_full_name_with_database_no_catalog():
    """Test full_name with database and table, no explicit catalog (uses default)."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(database_name="my_db", table_name="my_table")

        # Empty catalog is reset to internal, so result includes it
        assert result == "`internal`.`my_db`.`my_table`"


def test_full_name_table_only():
    """Test full_name with table only."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(table_name="my_table")

        assert result == "`my_table`"


def test_full_name_resets_empty_catalog_to_default():
    """Test full_name resets empty catalog to default."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(catalog_name="", database_name="db", table_name="table")

        # Empty catalog is reset to internal
        assert result == "`internal`.`db`.`table`"


@pytest.mark.acceptance
def test_full_name_quotes_identifiers():
    """Test full_name adds backticks to identifiers."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(catalog_name="catalog", database_name="database", table_name="table")

        assert result.count("`") == 6  # 3 pairs of backticks


def test_full_name_with_special_characters():
    """Test full_name with special characters in names."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(
            catalog_name="test-catalog",
            database_name="test_db",
            table_name="test-table",
        )

        assert "`test-catalog`" in result
        assert "`test_db`" in result
        assert "`test-table`" in result


def test_full_name_escapes_backticks():
    """Test full_name escapes embedded identifier quoting characters."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        result = connector.full_name(catalog_name="cat`alog", database_name="db`name", table_name="ta`ble")

        assert result == "`cat``alog`.`db``name`.`ta``ble`"


# ==================== _sqlalchemy_schema() Tests ====================


def test_sqlalchemy_schema_with_catalog_and_database():
    """Test _sqlalchemy_schema returns catalog.database format."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.database_name = "my_db"
        connector.catalog_name = "my_catalog"

        result = connector._sqlalchemy_schema(catalog_name="test_catalog", database_name="test_db")

        assert result == "test_catalog.test_db"


def test_sqlalchemy_schema_with_catalog_only():
    """Test _sqlalchemy_schema returns None when no database."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.database_name = None
        connector.catalog_name = "my_catalog"

        result = connector._sqlalchemy_schema(catalog_name="test_catalog")

        assert result is None


def test_sqlalchemy_schema_uses_catalog_without_registry_state():
    """Test _sqlalchemy_schema always includes catalog for direct connector usage."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.catalog_name = "my_catalog"
        connector.database_name = "my_db"

        result = connector._sqlalchemy_schema(database_name="test_db")

        assert result == "my_catalog.test_db"


def test_sqlalchemy_schema_uses_default_catalog():
    """Test _sqlalchemy_schema uses default catalog when not specified."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.database_name = "my_db"
        connector.catalog_name = None

        result = connector._sqlalchemy_schema(database_name="test_db")

        assert "internal" in result
        assert result == "internal.test_db"


# ==================== close() Method PyMySQL Error Handling Tests ====================


@pytest.mark.acceptance
def test_do_switch_context_catalog():
    """do_switch_context executes Doris SWITCH on the given connection."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_conn = MagicMock()

        connector.do_switch_context(mock_conn, catalog_name="new_catalog")

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

        sql_arg = str(mock_conn.execute.call_args[0][0].text)
        assert "SWITCH" in sql_arg
        assert "new_catalog" in sql_arg


def test_do_switch_context_database():
    """do_switch_context executes USE on the given connection."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_conn = MagicMock()

        connector.do_switch_context(mock_conn, database_name="new_db")

        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

        sql_arg = str(mock_conn.execute.call_args[0][0].text)
        assert "USE" in sql_arg
        assert "new_db" in sql_arg


def test_do_switch_context_catalog_and_database():
    """do_switch_context handles both catalog and database on the given connection."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_conn = MagicMock()

        connector.do_switch_context(mock_conn, catalog_name="cat", database_name="db")

        # Two execute calls: SWITCH + USE
        assert mock_conn.execute.call_count == 2
        assert mock_conn.commit.call_count == 2


def test_switch_catalog_statement():
    """connector.execute('switch ...') updates catalog_name via execute_content_set."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_conn = MagicMock()
        engine = MagicMock()
        engine.connect.return_value = mock_conn
        connector.engine = engine
        connector._owns_engine = True
        assert connector.catalog_name == "internal"

        with patch("datus_db_core.base.parse_sql_type", return_value=SQLType.UNKNOWN):
            connector.execute(input_params={"sql_query": "switch cat"})
            assert connector.catalog_name == "cat"

            connector.execute(input_params={"sql_query": "switch internal"})
            assert connector.catalog_name == "internal"


def test_use_catalog_database_updates_both_context_levels():
    """USE catalog.database keeps Doris context correct across core versions."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_conn = MagicMock()
        engine = MagicMock()
        engine.connect.return_value = mock_conn
        connector.engine = engine
        connector._owns_engine = True

        result = connector.execute_content_set("USE `hive-catalog`.`analytics`")

        assert result.success
        assert connector.catalog_name == "hive-catalog"
        assert connector.database_name == "analytics"


def test_init_normalizes_def_catalog():
    """config.catalog='def' should be treated as default (no catalog switch needed)."""
    config = DorisConfig(username="test_user", catalog="def", database="mydb")

    with patch("datus_mysql.MySQLConnector.__init__") as mock_init:
        connector = DorisConnector(config)

        # 'def' is default, so database should be in the MySQL connection string
        mysql_config = mock_init.call_args[0][0]
        assert mysql_config.database == "mydb"
        assert connector._deferred_database == ""


def test_close_ignores_struct_pack_error():
    """Test close ignores struct.pack error."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.engine = None

        with patch(
            "datus_mysql.MySQLConnector.close",
            side_effect=Exception("struct.pack error"),
        ):
            # Should not raise exception
            connector.close()
            assert connector.engine is None


def test_close_ignores_com_quit_error():
    """Test close ignores COM_QUIT error."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.engine = None

        with patch(
            "datus_mysql.MySQLConnector.close",
            side_effect=Exception("COMMAND.COM_QUIT failed"),
        ):
            # Should not raise exception
            connector.close()
            assert connector.engine is None


def test_close_ignores_required_argument_error():
    """Test close ignores 'required argument is not an integer' error."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.engine = None

        with patch(
            "datus_mysql.MySQLConnector.close",
            side_effect=Exception("required argument is not an integer"),
        ):
            # Should not raise exception
            connector.close()
            assert connector.engine is None


def test_close_clears_engine_on_pymysql_error():
    """Test close clears engine on PyMySQL error."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.engine = None

        with patch("datus_mysql.MySQLConnector.close", side_effect=Exception("struct.error")):
            connector.close()
            assert connector.engine is None


def test_close_disposes_engine_on_error():
    """Test close disposes engine on PyMySQL error."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        mock_engine = MagicMock()
        connector.engine = mock_engine

        with patch("datus_mysql.MySQLConnector.close", side_effect=Exception("struct.pack")):
            connector.close()

            # Engine should be disposed and set to None
            mock_engine.dispose.assert_called_once()
            assert connector.engine is None


def test_close_reraises_unexpected_errors():
    """Test close reraises unexpected errors."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.engine = None

        with patch(
            "datus_mysql.MySQLConnector.close",
            side_effect=Exception("Unexpected error"),
        ):
            with pytest.raises(Exception, match="Unexpected error"):
                connector.close()


# ==================== Utility Method Tests ====================


def test_to_dict_includes_catalog():
    """Test to_dict includes catalog field."""
    config = DorisConfig(username="test_user", catalog="my_catalog")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.host = "localhost"
        connector.port = 9030
        connector.username = "test_user"
        connector.database_name = "testdb"

        result = connector.to_dict()

        assert result["db_type"] == "doris"
        assert result["catalog"] == "my_catalog"
        assert result["host"] == "localhost"
        assert result["port"] == 9030


def test_get_type_returns_doris():
    """Test get_type returns DORIS."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert connector.get_type() == "doris"


def test_context_manager_support():
    """Test connector supports context manager protocol."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)
        connector.connect = MagicMock()
        connector.close = MagicMock()

        # Test context manager
        with connector as conn:
            assert conn is connector
            connector.connect.assert_called_once()

        connector.close.assert_called_once()


# ==================== Mixin Interface Tests ====================


@pytest.mark.acceptance
def test_implements_catalog_support_mixin():
    """Test connector implements CatalogSupportMixin."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert isinstance(connector, CatalogSupportMixin)


def test_implements_materialized_view_support_mixin():
    """Test connector implements MaterializedViewSupportMixin."""
    config = DorisConfig(username="test_user")

    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        connector = DorisConnector(config)

        assert isinstance(connector, MaterializedViewSupportMixin)
