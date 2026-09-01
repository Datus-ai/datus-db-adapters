# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

import datus_snowflake
from datus_db_core import ConnectorRegistry, MaterializedViewSupportMixin, SchemaNamespaceMixin, connector_registry
from datus_db_core.sql_utils import metadata_identifier
from datus_snowflake import SnowflakeConnector


class RecordingConnection:
    def __init__(self):
        self.cursor_obj = RecordingCursor()

    def cursor(self):
        return self.cursor_obj


class RecordingCursor:
    def __init__(self):
        self.executed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql):
        self.executed = True


def test_register_declares_database_schema_without_catalog():
    saved = ConnectorRegistry._capabilities.copy()
    try:
        datus_snowflake.register()
        assert connector_registry.support_database("snowflake")
        assert connector_registry.support_schema("snowflake")
        assert not connector_registry.support_catalog("snowflake")
    finally:
        ConnectorRegistry._capabilities = saved


def test_metadata_identifier_ignores_catalog_after_register():
    saved = ConnectorRegistry._capabilities.copy()
    try:
        datus_snowflake.register()
        assert (
            metadata_identifier(
                catalog_name="catalog",
                database_name="database",
                schema_name="schema",
                table_name="table",
                dialect="snowflake",
            )
            == "database.schema.table"
        )
    finally:
        ConnectorRegistry._capabilities = saved


def test_connector_implements_namespace_mixins():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)

    assert isinstance(connector, SchemaNamespaceMixin)
    assert isinstance(connector, MaterializedViewSupportMixin)


def test_sample_data_is_not_treated_as_system_database():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    assert connector._sys_databases() == {"SNOWFLAKE"}


def test_full_name_with_database_and_schema():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)

    full_name = connector.full_name(database_name="mydb", schema_name="myschema", table_name="mytable")

    assert full_name == '"mydb"."myschema"."mytable"'


def test_full_name_with_schema_only():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)

    full_name = connector.full_name(schema_name="myschema", table_name="mytable")

    assert full_name == '"myschema"."mytable"'


def test_full_name_with_table_only():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)

    full_name = connector.full_name(table_name="mytable")

    assert full_name == '"mytable"'


def test_catalog_parameter_is_rejected():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)

    with pytest.raises(ValueError, match="does not support a catalog namespace"):
        connector.full_name(catalog_name="cat", database_name="db", schema_name="schema", table_name="table")


def test_context_set_rejects_catalog_before_execution():
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.dialect = "snowflake"
    connector.connection = RecordingConnection()

    result = connector.execute_content_set("USE CATALOG cat")

    assert not result.success
    assert "does not support a catalog namespace" in result.error
    assert not connector.connection.cursor_obj.executed
