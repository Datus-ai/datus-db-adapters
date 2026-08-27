# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import patch

import pytest

from datus_db_core import DatusDbException, connector_registry
from datus_mysql import MySQLConnector
from datus_tidb import TiDBConfig, TiDBConnector, register


def _connector(**overrides) -> TiDBConnector:
    """Build a connector for real: MySQLConnector.__init__ only assembles the
    connection string, so nothing here contacts a server."""
    params = {"username": "test_user", "database": "analytics"}
    params.update(overrides)
    return TiDBConnector(TiDBConfig(**params))


@pytest.fixture
def connector() -> TiDBConnector:
    return _connector()


@pytest.mark.acceptance
@pytest.mark.parametrize(
    "config",
    [
        TiDBConfig(username="test_user", database="analytics"),
        {"username": "test_user", "database": "analytics"},
    ],
)
def test_connector_initialization(config):
    connector = TiDBConnector(config)

    assert isinstance(connector.tidb_config, TiDBConfig)
    assert connector.dialect == "tidb"
    assert connector.database_name == "analytics"


def test_connector_rejects_invalid_config_type():
    with pytest.raises(TypeError, match="config must be TiDBConfig or dict"):
        TiDBConnector("invalid_config")


def test_connection_string_targets_the_configured_tidb_port():
    connector = _connector(host="tidb.internal", port=4000, password="p@ss word")

    assert connector.connection_string.startswith("mysql+pymysql://test_user:")
    assert "@tidb.internal:4000/analytics" in connector.connection_string
    # Special characters must survive as percent-encoding, not break the URL.
    assert "p%40ss+word" in connector.connection_string


def test_sys_databases_add_metrics_schema_to_the_mysql_set(connector):
    sys_databases = connector._sys_databases()

    assert "metrics_schema" in sys_databases
    assert {"mysql", "information_schema", "performance_schema", "sys"} <= sys_databases


def test_sys_schemas_track_sys_databases(connector):
    """MySQL treats database and schema as the same level; TiDB inherits that."""
    assert connector._sys_schemas() == connector._sys_databases()


def test_materialized_view_metadata_is_refused_with_a_readable_reason(connector):
    """TiDB has no MATERIALIZED_VIEWS table; the inherited lookup would raise a
    bare 1146 that reads like a broken connector."""
    with pytest.raises(DatusDbException, match="no materialized views"):
        connector._get_metadata(table_type="mv")


@pytest.mark.parametrize("table_type", ["table", "view"])
def test_supported_table_types_reach_the_inherited_lookup(connector, table_type):
    with patch.object(MySQLConnector, "_get_metadata", return_value=[]) as inherited:
        assert connector._get_metadata(table_type=table_type, database_name="analytics") == []

    inherited.assert_called_once_with(table_type, "", "analytics")


def test_identifiers_use_backticks(connector):
    assert connector.quote_identifier("order") == "`order`"
    assert connector.quote_identifier("we`ird") == "`we``ird`"


def test_full_name_is_two_level(connector):
    """TiDB has no catalog level: database.table, never catalog.database.table."""
    assert connector.full_name(database_name="analytics", table_name="orders") == "`analytics`.`orders`"
    assert connector.full_name(table_name="orders") == "`orders`"


def test_get_type_and_to_dict_report_tidb(connector):
    assert connector.get_type() == "tidb"
    assert connector.to_dict() == {
        "db_type": "tidb",
        "host": "127.0.0.1",
        "port": 4000,
        "user": "test_user",
        "database": "analytics",
    }


def test_registration_exposes_tidb_with_the_mysql_parser_dialect():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()

        metadata = connector_registry.get_metadata("tidb")
        assert metadata.connector_class is TiDBConnector
        assert metadata.config_class is TiDBConfig
        assert metadata.display_name == "TiDB"
        # sqlglot has no TiDB dialect; MySQL is the closest parser.
        assert connector_registry.get_parser_dialect("tidb") == "mysql"
        assert connector_registry.get_capabilities("tidb") == {"database"}
        assert connector_registry.support_catalog("tidb") is False
        assert connector_registry.support_schema("tidb") is False
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
