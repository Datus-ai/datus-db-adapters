# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock

import pytest

from datus_hive import HiveConfig, HiveConnector


@pytest.fixture
def metadata_engine():
    """Exercise metadata parsing through the SQLAlchemy connection boundary."""
    connector = HiveConnector(HiveConfig(username="hive", database="current"))
    connector.engine = MagicMock()
    connector._owns_engine = True
    connection = connector.engine.connect.return_value
    responses = {}

    def execute(statement):
        sql = str(statement)
        if sql.startswith("USE "):
            return MagicMock()
        rows = responses[sql]
        if isinstance(rows, Exception):
            raise rows
        result = MagicMock()
        result.fetchall.return_value = [MagicMock(**{"_asdict.return_value": row}) for row in rows]
        return result

    connection.execute.side_effect = execute
    try:
        yield connector, responses, connection
    finally:
        connector.close()


@pytest.mark.parametrize(
    "database,table_column,view_column",
    [("analytics", "tab_name", "tab_name"), ("data`base", "tableName", "viewName"), ("", "tab_name", "tab_name")],
)
def test_table_listing_excludes_views_before_qualification(metadata_engine, database, table_column, view_column):
    connector, responses, connection = metadata_engine
    resolved_database = database or "current"
    quoted_database = connector.quote_identifier(resolved_database)
    responses[f"SHOW TABLES IN {quoted_database}"] = [{table_column: "base_table"}, {table_column: "report_view"}]
    responses[f"SHOW VIEWS IN {quoted_database}"] = [{view_column: "report_view"}]

    assert connector.get_tables(database_name=database) == ["base_table" if database else "current.base_table"]
    executed = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert f"SHOW TABLES IN {quoted_database}" in executed
    assert f"SHOW VIEWS IN {quoted_database}" in executed


@pytest.mark.parametrize("view_names", [[], ["report_view"], ["REPORT_VIEW"]])
def test_table_listing_matches_view_names_case_insensitively(metadata_engine, view_names):
    connector, responses, _ = metadata_engine
    responses["SHOW TABLES IN `current`"] = [{"tab_name": "base_table"}, {"tab_name": "report_view"}]
    responses["SHOW VIEWS IN `current`"] = [{"tab_name": name} for name in view_names]

    expected = ["base_table"] if view_names else ["base_table", "report_view"]
    assert connector.get_tables(database_name="current") == expected


def test_table_ddl_listing_does_not_label_views_as_tables(metadata_engine):
    connector, responses, _ = metadata_engine
    responses["SHOW TABLES IN `current`"] = [{"tab_name": "base_table"}, {"tab_name": "report_view"}]
    responses["SHOW VIEWS IN `current`"] = [{"tab_name": "report_view"}]
    ddl = "CREATE TABLE base_table (id INT)"
    responses["SHOW CREATE TABLE `current`.`base_table`"] = [{"createtab_stmt": ddl}]

    assert connector.get_tables_with_ddl(database_name="current") == [
        {
            "identifier": "current.base_table",
            "catalog_name": "",
            "schema_name": "",
            "database_name": "current",
            "table_name": "base_table",
            "table_type": "table",
            "definition": ddl,
        }
    ]


@pytest.mark.parametrize(
    "columns",
    [
        ("col_name", "data_type", "comment"),
        ("col_name", "data_type", "default_value", "not_null", "unique", "comment"),
        ("COMMENT", "NOT_NULL", "COL_NAME", "DEFAULT_VALUE", "DATA_TYPE", "UNIQUE"),
    ],
    ids=["apache-hive", "inceptor", "reordered-mixed-case"],
)
def test_column_metadata_uses_names_instead_of_positions(metadata_engine, columns):
    connector, responses, _ = metadata_engine
    row = {
        "col_name": "label",
        "data_type": "string",
        "default_value": "NULL",
        "not_null": "No",
        "unique": "No",
        "comment": "中文注释",
    }
    responses["DESCRIBE `current`.`base_table`"] = [{name: row[name.lower()] for name in columns}]

    assert connector.get_schema(table_name="base_table") == [
        {
            "cid": 0,
            "name": "label",
            "type": "string",
            "comment": "中文注释",
            "nullable": True,
            "pk": False,
            "default_value": None,
        }
    ]


def test_column_metadata_without_comment_does_not_use_default_value(metadata_engine):
    connector, responses, _ = metadata_engine
    responses["DESCRIBE `current`.`base_table`"] = [{"col_name": "id", "data_type": "int", "default_value": "42"}]

    columns = connector.get_schema(table_name="base_table")
    assert [(column["name"], column["type"], column["comment"]) for column in columns] == [("id", "int", None)]
