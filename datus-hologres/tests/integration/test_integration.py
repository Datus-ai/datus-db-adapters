# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import os
import uuid

import pandas as pd
import pyarrow as pa
import pytest

from datus_hologres import HologresConnector
from datus_hologres.handlers import parse_hologres_identifier


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_version_and_namespaces(connector: HologresConnector):
    result = connector.execute(
        {
            "sql_query": (
                "SELECT current_database() AS database_name, current_schema() AS schema_name, "
                "hg_version() AS hologres_version"
            )
        },
        result_format="list",
    )
    _assert_success(result, "version query")
    row = result.sql_return[0]
    assert row["database_name"] == connector.database_name
    assert row["schema_name"] == connector.schema_name
    assert "Hologres" in row["hologres_version"]

    schemas = connector.get_schemas()
    assert connector.schema_name in schemas
    assert all(not connector._is_system_schema(schema) for schema in schemas)
    assert connector.database_name in connector.get_databases()


@pytest.mark.integration
def test_result_formats(connector: HologresConnector):
    query = {"sql_query": "SELECT 1 AS id, 'alpha'::text AS name"}

    list_result = connector.execute(query, result_format="list")
    csv_result = connector.execute(query, result_format="csv")
    pandas_result = connector.execute(query, result_format="pandas")
    arrow_result = connector.execute(query, result_format="arrow")

    for name, result in (
        ("list", list_result),
        ("csv", csv_result),
        ("pandas", pandas_result),
        ("arrow", arrow_result),
    ):
        _assert_success(result, f"{name} format query")

    assert list_result.sql_return == [{"id": 1, "name": "alpha"}]
    assert "id,name" in csv_result.sql_return
    assert isinstance(pandas_result.sql_return, pd.DataFrame)
    assert isinstance(arrow_result.sql_return, pa.Table)


@pytest.mark.integration
@pytest.mark.acceptance
def test_metadata_ddl_and_three_part_identifier(connector: HologresConnector):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"metadata_{suffix}"
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"
    full_ref = f"{q(connector.database_name)}.{table_ref}"

    create = connector.execute_ddl(
        f"""
        CREATE TABLE {table_ref} (
            id BIGINT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            payload TEXT,
            PRIMARY KEY (id)
        )
        WITH (
            orientation = 'column',
            distribution_key = 'id',
            event_time_column = 'occurred_at'
        )
        """
    )
    _assert_success(create, "create metadata table")
    try:
        insert = connector.execute_insert(
            f"INSERT INTO {table_ref} VALUES (1, TIMESTAMPTZ '2024-01-01 00:00:00+00', 'alpha')"
        )
        _assert_success(insert, "insert metadata row")

        query = connector.execute({"sql_query": f"SELECT payload FROM {full_ref} WHERE id = 1"}, result_format="list")
        _assert_success(query, "three-part identifier query")
        assert query.sql_return == [{"payload": "alpha"}]

        tables = connector.get_tables(schema_name=connector.schema_name)
        assert f"{connector.database_name}.{connector.schema_name}.{table_name}" in tables

        schema = connector.get_schema(schema_name=connector.schema_name, table_name=table_name)
        assert [column["name"] for column in schema] == ["id", "occurred_at", "payload"]
        assert schema[0]["pk"] is True

        metadata = connector.get_tables_with_ddl(schema_name=connector.schema_name, tables=[table_name])
        assert len(metadata) == 1
        ddl = metadata[0]["definition"]
        assert "PRIMARY KEY" in ddl
        assert "orientation = 'column'" in ddl
        assert "distribution_key = 'id'" in ddl
        assert "event_time_column = 'occurred_at'" in ddl

        samples = connector.get_sample_rows(
            schema_name=connector.schema_name,
            tables=[table_name],
            top_n=1,
        )
        assert len(samples) == 1
        assert "alpha" in samples[0]["sample_rows"]
    finally:
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop metadata table")


@pytest.mark.integration
@pytest.mark.acceptance
def test_generated_binlog_ddl_round_trip(connector: HologresConnector):
    table_name = f"binlog_ddl_{uuid.uuid4().hex[:8]}"
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"

    create = connector.execute_ddl(
        f"""
        CREATE TABLE {table_ref} (
            id BIGINT NOT NULL,
            payload TEXT,
            PRIMARY KEY (id)
        )
        WITH (
            orientation = 'column',
            distribution_key = 'id',
            binlog_level = 'replica',
            binlog_ttl = '86400'
        )
        """
    )
    _assert_success(create, "create binlog DDL source table")
    try:
        metadata = connector.get_tables_with_ddl(
            schema_name=connector.schema_name,
            tables=[table_name],
        )
        assert len(metadata) == 1
        ddl = metadata[0]["definition"]
        assert "binlog_level = 'replica'" in ddl
        assert "binlog_ttl = '86400'" in ddl
        assert "binlog.level" not in ddl
        assert "binlog.ttl" not in ddl

        _assert_success(connector.execute_ddl(f"DROP TABLE {table_ref}"), "drop binlog DDL source table")
        _assert_success(connector.execute_ddl(ddl), "recreate table from generated binlog DDL")

        properties = connector._get_table_properties(connector.schema_name, table_name)
        assert properties["binlog_level"] == "replica"
        assert properties["binlog_ttl"] == "86400"
    finally:
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop binlog DDL table")


@pytest.mark.integration
def test_special_identifier_listing_round_trip(connector: HologresConnector):
    table_name = f'Order.Items"{uuid.uuid4().hex[:8]}'
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"

    _assert_success(connector.execute_ddl(f"CREATE TABLE {table_ref} (id INTEGER)"), "create quoted-name table")
    try:
        listed = connector.get_tables(schema_name=connector.schema_name)
        parsed = [parse_hologres_identifier(name) for name in listed]
        assert any(item["table_name"] == table_name for item in parsed)

        metadata = connector._get_metadata("table", schema_name=connector.schema_name)
        item = next(item for item in metadata if item["table_name"] == table_name)
        assert parse_hologres_identifier(item["identifier"])["table_name"] == table_name
    finally:
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop quoted-name table")


@pytest.mark.integration
def test_secondary_database_schema_routing(connector: HologresConnector):
    secondary_database = os.getenv("HOLOGRES_SECONDARY_DATABASE", "").strip()
    if not secondary_database:
        pytest.skip("HOLOGRES_SECONDARY_DATABASE is required for cross-database schema coverage")
    assert secondary_database != connector.database_name

    result = connector.execute_query(
        "SELECT current_database() AS database_name",
        result_format="list",
        database_name=secondary_database,
    )
    _assert_success(result, "secondary database connection")
    assert result.sql_return == [{"database_name": secondary_database}]

    schemas = connector.get_schemas(database_name=secondary_database, include_sys=True)
    expected_schema = os.getenv("HOLOGRES_SECONDARY_SCHEMA", "").strip()
    if expected_schema:
        assert expected_schema in schemas
    else:
        assert schemas


@pytest.mark.integration
def test_preconfigured_foreign_table_metadata(connector: HologresConnector):
    foreign_identifier = os.getenv("HOLOGRES_TEST_FOREIGN_TABLE", "").strip()
    if not foreign_identifier:
        pytest.skip("HOLOGRES_TEST_FOREIGN_TABLE is required for live foreign-table coverage")

    coordinate = parse_hologres_identifier(foreign_identifier)
    database = coordinate["database_name"] or connector.database_name
    schema = coordinate["schema_name"] or connector.schema_name
    table = coordinate["table_name"]

    metadata = connector._get_metadata(
        "table",
        database_name=database,
        schema_name=schema,
    )
    item = next(item for item in metadata if item["table_name"] == table)
    assert item["identifier"] == connector.identifier(
        database_name=database,
        schema_name=schema,
        table_name=table,
    )

    ddl_items = connector.get_tables_with_ddl(
        database_name=database,
        schema_name=schema,
        tables=[table],
    )
    assert len(ddl_items) == 1
    ddl = ddl_items[0]["definition"]
    assert ddl.startswith("CREATE FOREIGN TABLE")
    assert "\nSERVER " in ddl
    assert "\nOPTIONS (" in ddl


@pytest.mark.integration
def test_view_metadata(connector: HologresConnector):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"view_base_{suffix}"
    view_name = f"view_{suffix}"
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"
    view_ref = f"{q(connector.schema_name)}.{q(view_name)}"

    _assert_success(connector.execute_ddl(f"CREATE TABLE {table_ref} (id INTEGER)"), "create view base")
    try:
        _assert_success(connector.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT id FROM {table_ref}"), "create view")
        views = connector.get_views(schema_name=connector.schema_name)
        assert f"{connector.database_name}.{connector.schema_name}.{view_name}" in views
        view_metadata = connector.get_views_with_ddl(schema_name=connector.schema_name)
        definition = next(item["definition"] for item in view_metadata if item["table_name"] == view_name)
        assert definition.startswith("CREATE VIEW")
        assert table_name in definition
    finally:
        _assert_success(connector.execute_ddl(f"DROP VIEW IF EXISTS {view_ref}"), "drop view")
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop view base")


@pytest.mark.integration
@pytest.mark.acceptance
def test_primary_key_update_and_delete(connector: HologresConnector):
    table_name = f"crud_{uuid.uuid4().hex[:8]}"
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"
    _assert_success(
        connector.execute_ddl(
            f"""
            CREATE TABLE {table_ref} (
                id BIGINT NOT NULL PRIMARY KEY,
                value TEXT
            )
            WITH (orientation = 'column', distribution_key = 'id')
            """
        ),
        "create CRUD table",
    )
    try:
        _assert_success(connector.execute_insert(f"INSERT INTO {table_ref} VALUES (1, 'one'), (2, 'two')"), "insert")
        update = connector.execute_update(f"UPDATE {table_ref} SET value = 'updated' WHERE id = 1")
        delete = connector.execute_delete(f"DELETE FROM {table_ref} WHERE id = 2")
        _assert_success(update, "update")
        _assert_success(delete, "delete")

        rows = connector.execute({"sql_query": f"SELECT id, value FROM {table_ref}"}, result_format="list")
        _assert_success(rows, "select CRUD result")
        assert rows.sql_return == [{"id": 1, "value": "updated"}]
    finally:
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop CRUD table")


@pytest.mark.integration
def test_materialized_view_metadata(connector: HologresConnector):
    materialized_views = connector.get_materialized_views(schema_name=connector.schema_name)
    assert isinstance(materialized_views, list)
    if os.getenv("HOLOGRES_TEST_MATERIALIZED_VIEW", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("Materialized views are not supported by Hologres Serverless instances")

    suffix = uuid.uuid4().hex[:8]
    table_name = f"mv_base_{suffix}"
    mv_name = f"mv_{suffix}"
    q = connector.quote_identifier
    table_ref = f"{q(connector.schema_name)}.{q(table_name)}"
    mv_ref = f"{q(connector.schema_name)}.{q(mv_name)}"

    _assert_success(
        connector.execute_ddl(f"CREATE TABLE {table_ref} (category TEXT, amount BIGINT)"),
        "create materialized view base",
    )
    try:
        _assert_success(
            connector.execute_insert(f"INSERT INTO {table_ref} VALUES ('a', 10), ('a', 20), ('b', 5)"),
            "load materialized view base",
        )
        create = connector.execute_ddl(
            f"CREATE MATERIALIZED VIEW {mv_ref} AS "
            f"SELECT category, SUM(amount) AS total FROM {table_ref} GROUP BY category"
        )
        _assert_success(create, "create materialized view")

        materialized_views = connector.get_materialized_views(schema_name=connector.schema_name)
        assert f"{connector.database_name}.{connector.schema_name}.{mv_name}" in materialized_views
        metadata = connector._get_objects_with_ddl(
            "mv",
            schema_name=connector.schema_name,
        )
        definition = next(item["definition"] for item in metadata if item["table_name"] == mv_name)
        assert definition.startswith("CREATE MATERIALIZED VIEW")
        assert "sum(amount)" in definition.lower()
    finally:
        _assert_success(connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS {mv_ref}"), "drop materialized view")
        _assert_success(connector.execute_ddl(f"DROP TABLE IF EXISTS {table_ref}"), "drop materialized view base")
