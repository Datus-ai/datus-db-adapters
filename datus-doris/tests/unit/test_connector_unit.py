# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest

import datus_doris
from datus_db_core import SQLType
from datus_doris import DorisConfig, DorisConnector


def _connector(config=None, **overrides) -> DorisConnector:
    config = config or DorisConfig(username="test_user", **overrides)
    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        return DorisConnector(config)


@pytest.fixture
def connector() -> DorisConnector:
    return _connector()


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "config",
    [
        DorisConfig(username="test_user", catalog="internal"),
        {"username": "test_user", "catalog": "internal"},
    ],
)
def test_connector_initialization(config):
    connector = _connector(config=config)

    assert isinstance(connector.doris_config, DorisConfig)
    assert connector.catalog_name == "internal"
    assert connector.dialect == "doris"


def test_connector_rejects_invalid_config_type():
    with pytest.raises(TypeError, match="config must be DorisConfig or dict"):
        DorisConnector("invalid_config")


@pytest.mark.parametrize(
    ("catalog", "database", "mysql_database", "effective_catalog"),
    [
        ("internal", "analytics", "analytics", "internal"),
        ("def", "analytics", "analytics", "internal"),
        ("external", "analytics", "", "external"),
    ],
)
def test_connector_maps_doris_config_to_mysql(
    catalog,
    database,
    mysql_database,
    effective_catalog,
):
    config = DorisConfig(
        host="doris.example",
        port=9031,
        username="admin",
        password="secret",
        catalog=catalog,
        database=database,
    )

    with patch("datus_mysql.MySQLConnector.__init__") as parent_init:
        connector = DorisConnector(config)

    mysql_config = parent_init.call_args.args[0]
    assert (mysql_config.host, mysql_config.port, mysql_config.username) == (
        "doris.example",
        9031,
        "admin",
    )
    assert mysql_config.database == mysql_database
    assert connector.catalog_name == effective_catalog
    assert connector.database_name == database


@pytest.mark.parametrize(
    ("catalog", "database", "expected"),
    [
        ("internal", "analytics", {"catalog_name": "internal", "database_name": "analytics", "schema_name": ""}),
        ("def", "analytics", {"catalog_name": "internal", "database_name": "analytics", "schema_name": ""}),
        (
            "external",
            "analytics",
            {"catalog_name": "external", "database_name": "analytics", "schema_name": ""},
        ),
    ],
)
def test_get_current_context(catalog, database, expected):
    connector = _connector(catalog=catalog, database=database)
    assert connector.get_current_context() == expected


@pytest.mark.parametrize(
    ("configured_catalog", "requested_catalog", "expected"),
    [
        ("internal", "", "internal"),
        ("internal", "def", "internal"),
        ("configured", "", "configured"),
        ("configured", "explicit", "explicit"),
    ],
)
def test_resolve_catalog(configured_catalog, requested_catalog, expected):
    connector = _connector(catalog=configured_catalog)
    assert connector._resolve_catalog(requested_catalog) == expected


def test_switch_catalog_delegates_and_clears_database(connector):
    connector.database_name = "stale_database"
    connector.switch_context = MagicMock()

    connector.switch_catalog("external")

    connector.switch_context.assert_called_once_with(catalog_name="external")
    assert connector.database_name == ""


@pytest.mark.parametrize(
    ("catalog", "database", "table", "expected"),
    [
        ("catalog", "database", "table", "`catalog`.`database`.`table`"),
        ("", "database", "table", "`internal`.`database`.`table`"),
        ("catalog", "", "table", "`table`"),
        ("", "", "table", "`table`"),
        ("cat`alog", "db`name", "ta`ble", "`cat``alog`.`db``name`.`ta``ble`"),
    ],
)
def test_full_name(connector, catalog, database, table, expected):
    assert (
        connector.full_name(
            catalog_name=catalog,
            database_name=database,
            table_name=table,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("catalog", "database", "expected"),
    [
        ("catalog", "database", "catalog.database"),
        ("catalog", "", None),
        ("", "database", "internal.database"),
    ],
)
def test_sqlalchemy_schema(connector, catalog, database, expected):
    assert connector._sqlalchemy_schema(catalog_name=catalog, database_name=database) == expected


@pytest.mark.parametrize(
    ("catalog", "database", "expected_sql"),
    [
        ("external", "", ["SWITCH `external`"]),
        ("", "analytics", ["USE `analytics`"]),
        ("external", "analytics", ["SWITCH `external`", "USE `analytics`"]),
    ],
)
def test_do_switch_context(connector, catalog, database, expected_sql):
    conn = MagicMock()

    connector.do_switch_context(conn, catalog_name=catalog, database_name=database)

    actual_sql = [str(call.args[0].text) for call in conn.execute.call_args_list]
    assert actual_sql == expected_sql
    assert conn.commit.call_count == len(expected_sql)


def test_configured_external_database_is_applied_on_checkout():
    connector = _connector(catalog="external", database="analytics")
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = conn
    connector.engine = engine
    connector._owns_engine = True

    with connector._conn():
        pass

    actual_sql = [str(call.args[0].text) for call in conn.execute.call_args_list]
    assert actual_sql == ["SWITCH `external`", "USE `analytics`"]


def test_metadata_literals_escape_backslashes_before_quotes(connector):
    rows = MagicMock()
    rows.__len__.return_value = 0
    connector.connect = MagicMock()
    connector._execute_pandas = MagicMock(return_value=rows)
    connector._get_materialized_view_metadata = MagicMock(return_value=[])
    unsafe_name = "db\\'name"

    connector._get_metadata(database_name=unsafe_name)
    metadata_query = connector._execute_pandas.call_args.args[0]
    assert "TABLE_SCHEMA = 'db\\\\''name'" in metadata_query

    connector.get_schema(database_name=unsafe_name, table_name=unsafe_name)
    schema_query = connector._execute_pandas.call_args.args[0]
    assert "TABLE_SCHEMA = 'db\\\\''name'" in schema_query
    assert "TABLE_NAME = 'db\\\\''name'" in schema_query


def _connector_with_engine() -> DorisConnector:
    connector = _connector()
    engine = MagicMock()
    engine.connect.return_value = MagicMock()
    connector.engine = engine
    connector._owns_engine = True
    return connector


def test_switch_statement_routes_around_older_core_classifier():
    connector = _connector_with_engine()

    with patch("datus_db_core.base.parse_sql_type", return_value=SQLType.UNKNOWN):
        result = connector.execute(input_params={"sql_query": "SWITCH external"})

    assert result.success
    assert connector.catalog_name == "external"
    assert connector.database_name == ""


def test_use_catalog_database_updates_both_context_levels():
    connector = _connector_with_engine()

    result = connector.execute_content_set("USE `hive-catalog`.`analytics`")

    assert result.success
    assert connector.catalog_name == "hive-catalog"
    assert connector.database_name == "analytics"


@pytest.mark.parametrize(
    "message",
    [
        "struct.error",
        "struct.pack error",
        "COMMAND.COM_QUIT failed",
        "required argument is not an integer",
    ],
)
def test_close_disposes_engine_for_known_pymysql_errors(connector, message):
    connector.engine = MagicMock()

    with patch("datus_mysql.MySQLConnector.close", side_effect=Exception(message)):
        connector.close()

    assert connector.engine is None
    assert connector._owns_engine is False


def test_close_reraises_unexpected_errors(connector):
    with (
        patch("datus_mysql.MySQLConnector.close", side_effect=Exception("unexpected")),
        pytest.raises(Exception, match="unexpected"),
    ):
        connector.close()


def test_serialization_and_type(connector):
    connector.host = "localhost"
    connector.port = 9030
    connector.username = "test_user"
    connector.database_name = "analytics"

    assert connector.to_dict() == {
        "db_type": "doris",
        "host": "localhost",
        "port": 9030,
        "user": "test_user",
        "catalog": "internal",
        "database": "analytics",
    }
    assert connector.get_type() == "doris"


def test_context_manager_connects_and_closes(connector):
    connector.connect = MagicMock()
    connector.close = MagicMock()

    with connector as current:
        assert current is connector

    connector.connect.assert_called_once_with()
    connector.close.assert_called_once_with()


def test_registers_doris_adapter():
    with patch("datus_db_core.connector_registry.register") as register:
        datus_doris.register()

    register.assert_called_once_with(
        "doris",
        DorisConnector,
        config_class=DorisConfig,
        capabilities={"catalog", "database"},
    )
