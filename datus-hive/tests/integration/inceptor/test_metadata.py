"""Strict manual metadata contracts for the Hive adapter against Inceptor.

Connection/setup errors fail the run. They are never converted into skipped tests.
Only the uniquely named database created by this session is removed afterwards.
"""

import csv
import io

import pytest

pytestmark = pytest.mark.integration


def test_databases_and_tables(connector, objects):
    database, table, _, _, _ = objects
    assert database in connector.get_databases()
    assert table in connector.get_tables(database_name=database)


def test_table_view_classification(connector, objects):
    database, table, view, _, _ = objects
    tables = connector.get_tables(database_name=database)
    assert table in tables
    assert view not in tables, f"View {view} was classified as a table: {tables}"


def test_columns(connector, objects):
    database, table, _, _, _ = objects
    columns = connector.get_schema(database_name=database, table_name=table)
    assert [(column["name"], column["type"].lower()) for column in columns] == [("id", "int"), ("label", "string")]
    assert [column["comment"] for column in columns] == ["identifier", "中文注释"]


def test_table_ddl(connector, objects):
    database, table, _, _, _ = objects
    definitions = connector.get_tables_with_ddl(database_name=database, tables=[table])
    assert len(definitions) == 1
    definition = definitions[0]
    assert definition["database_name"] == database
    assert definition["table_name"] == table
    assert definition["table_type"] == "table"
    assert "CREATE" in definition["definition"].upper()
    assert table in definition["definition"]


def test_views_and_view_ddl(connector, objects):
    database, _, view, _, _ = objects
    assert view in connector.get_views(database_name=database)
    definitions = connector.get_views_with_ddl(database_name=database)
    definition = next(item for item in definitions if item["table_name"] == view)
    assert definition["table_type"] == "view"
    assert "CREATE VIEW" in definition["definition"].upper()


def test_sample_rows(connector, objects):
    database, table, _, _, _ = objects
    samples = connector.get_sample_rows(database_name=database, tables=[table], top_n=1)
    assert len(samples) == 1
    assert samples[0]["table_name"] == table
    assert list(csv.DictReader(io.StringIO(samples[0]["sample_rows"]))) == [{"id": "1", "label": "本地联调"}]
