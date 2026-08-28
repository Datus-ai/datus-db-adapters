# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import os
import uuid

import pytest

from datus_db_core import DatusDbException
from datus_maxcompute import MaxComputeConfig, MaxComputeConnector

pytestmark = pytest.mark.integration


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for MaxCompute cloud integration tests")
    return value


def _connector(project: str) -> MaxComputeConnector:
    return MaxComputeConnector(
        MaxComputeConfig(
            project=project,
            endpoint=_required_env("MAXCOMPUTE_ENDPOINT"),
            access_key_id=_required_env("MAXCOMPUTE_ACCESS_KEY_ID"),
            access_key_secret=_required_env("MAXCOMPUTE_ACCESS_KEY_SECRET"),
            namespace_mode="auto",
            query_timeout_seconds=300,
        )
    )


def _assert_success(result, operation: str):
    assert result.success, f"{operation} failed: {result.error}"
    return result


def _drop(connector: MaxComputeConnector, sql: str, schema_name: str = "default") -> None:
    connector.execute_ddl(
        sql,
        database_name=connector.project,
        schema_name=schema_name,
    )


@pytest.mark.parametrize(
    ("project_env", "expected_mode", "expected_capabilities"),
    [
        ("MAXCOMPUTE_TWO_LEVEL_PROJECT", "two_level", {"database"}),
        ("MAXCOMPUTE_THREE_LEVEL_PROJECT", "three_level", {"database", "schema"}),
    ],
)
def test_namespace_detection_crud_and_metadata(project_env, expected_mode, expected_capabilities):
    connector = _connector(_required_env(project_env))
    table_name = f"datus_adapter_ci_{uuid.uuid4().hex[:12]}"
    schema_name = "default" if expected_mode == "three_level" else ""
    full_name = connector.full_name(schema_name=schema_name, table_name=table_name)

    assert connector.namespace_mode == expected_mode
    assert connector.get_effective_capabilities() == expected_capabilities

    try:
        create = connector.execute_ddl(
            f"CREATE TABLE {full_name} (id BIGINT, name STRING) LIFECYCLE 1",
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert create.success, create.error

        insert = connector.execute_insert(
            f"INSERT INTO TABLE {full_name} SELECT 1 AS id, 'alpha' AS name UNION ALL SELECT 2 AS id, 'beta' AS name",
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert insert.success, insert.error

        query = connector.execute_query(
            f"SELECT id, name FROM {full_name} ORDER BY id",
            result_format="list",
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert query.success, query.error
        assert query.sql_return == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]

        show = connector.execute(
            {"sql_query": "SHOW TABLES", "result_format": "list"},
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert show.success, show.error
        assert any(row["result"] == table_name or row["result"].endswith(f":{table_name}") for row in show.sql_return)

        explain = connector.execute(
            {
                "sql_query": f"EXPLAIN SELECT id FROM {full_name} LIMIT 1",
                "result_format": "list",
            },
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert explain.success, explain.error
        assert explain.sql_return and "job" in explain.sql_return[0]["result"].lower()

        preview_rows = list(
            connector.execute_csv_iterator(
                f"SELECT id, name FROM {full_name} ORDER BY id",
                max_rows=1,
            )
        )
        assert preview_rows == [("id", "name"), ("1", "alpha")]

        assert table_name in connector.get_tables(
            database_name=connector.project,
            schema_name=schema_name,
        )
        columns = connector.get_schema(
            database_name=connector.project,
            schema_name=schema_name,
            table_name=table_name,
        )
        assert [(column["name"], column["type"]) for column in columns] == [
            ("id", "BIGINT"),
            ("name", "STRING"),
        ]
        ddl_rows = connector.get_tables_with_ddl(
            database_name=connector.project,
            schema_name=schema_name,
            tables=[table_name],
        )
        assert len(ddl_rows) == 1
        assert table_name in ddl_rows[0]["definition"]
    finally:
        connector.execute_ddl(
            f"DROP TABLE IF EXISTS {full_name}",
            database_name=connector.project,
            schema_name=schema_name,
        )


def test_three_level_custom_schema_object_metadata_and_samples():
    connector = _connector(_required_env("MAXCOMPUTE_THREE_LEVEL_PROJECT"))
    prefix = f"datus_adapter_ci_{uuid.uuid4().hex[:12]}"
    schema_name = f"{prefix}_schema"
    table_name = f"{prefix}_table"
    scope_name = f"{prefix}_scope"
    view_name = f"{prefix}_view"
    mv_name = f"{prefix}_mv"
    schema_created = False

    def full(name: str, schema: str = schema_name) -> str:
        return connector.full_name(
            database_name=connector.project,
            schema_name=schema,
            table_name=name,
        )

    try:
        _assert_success(
            connector.execute_ddl(
                f"CREATE SCHEMA {schema_name}",
                database_name=connector.project,
                schema_name="default",
            ),
            "create schema",
        )
        schema_created = True
        assert schema_name in connector.get_schemas(database_name=connector.project)

        _assert_success(
            connector.execute_ddl(
                f"CREATE TABLE {full(table_name)} ("
                "id BIGINT, `select` STRING, amount DECIMAL(10,2), event_date DATE) LIFECYCLE 1",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "create typed table",
        )
        _assert_success(
            connector.execute_ddl(
                f"CREATE TABLE {full(scope_name)} (id BIGINT, marker STRING) LIFECYCLE 1",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "create custom-schema scope table",
        )
        _assert_success(
            connector.execute_ddl(
                f"CREATE TABLE {full(scope_name, 'default')} (id BIGINT) LIFECYCLE 1",
                database_name=connector.project,
                schema_name="default",
            ),
            "create default-schema scope table",
        )
        _assert_success(
            connector.execute_insert(
                f"INSERT OVERWRITE TABLE {full(table_name)} "
                "SELECT 1, 'alpha', CAST('12.34' AS DECIMAL(10,2)), CAST('2026-08-28' AS DATE) "
                "UNION ALL "
                "SELECT 2, '中文 beta', CAST('56.78' AS DECIMAL(10,2)), CAST('2026-08-29' AS DATE)",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "insert typed rows",
        )

        query = _assert_success(
            connector.execute_query(
                f"SELECT id, `select`, amount, event_date FROM {full(table_name)} ORDER BY id LIMIT 10",
                result_format="list",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "query typed rows",
        )
        assert query.row_count == 2
        assert query.sql_return[1]["select"] == "中文 beta"
        assert [
            (column["name"], column["type"])
            for column in connector.get_schema(
                database_name=connector.project,
                schema_name=schema_name,
                table_name=table_name,
            )
        ] == [
            ("id", "BIGINT"),
            ("select", "STRING"),
            ("amount", "DECIMAL(10,2)"),
            ("event_date", "DATE"),
        ]

        ddl_rows = connector.get_tables_with_ddl(
            database_name=connector.project,
            schema_name=schema_name,
            tables=[table_name, scope_name],
        )
        assert {row["table_name"] for row in ddl_rows} == {table_name, scope_name}
        assert all(row["schema_name"] == schema_name for row in ddl_rows)
        with pytest.raises(DatusDbException, match="outside requested scope"):
            connector.get_tables_with_ddl(
                database_name=connector.project,
                schema_name="default",
                tables=[f"{connector.project}.{schema_name}.{scope_name}"],
            )

        _assert_success(
            connector.execute_ddl(
                f"CREATE VIEW {full(view_name)} AS SELECT id, `select` AS label FROM {full(table_name)} WHERE id = 1",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "create view",
        )
        _assert_success(
            connector.execute_ddl(
                f"CREATE MATERIALIZED VIEW {full(mv_name)} LIFECYCLE 1 AS "
                f"SELECT id, `select` AS label FROM {full(table_name)}",
                database_name=connector.project,
                schema_name=schema_name,
            ),
            "create materialized view",
        )

        assert view_name in connector.get_views(database_name=connector.project, schema_name=schema_name)
        assert mv_name in connector.get_materialized_views(
            database_name=connector.project,
            schema_name=schema_name,
        )
        assert any(
            row["table_name"] == view_name and row["table_type"] == "view"
            for row in connector.get_views_with_ddl(database_name=connector.project, schema_name=schema_name)
        )
        assert any(
            row["table_name"] == mv_name and row["table_type"] == "mv"
            for row in connector.get_materialized_views_with_ddl(
                database_name=connector.project,
                schema_name=schema_name,
            )
        )

        for object_name, object_type in ((table_name, "table"), (view_name, "view"), (mv_name, "mv")):
            samples = connector.get_sample_rows(
                tables=[object_name],
                top_n=2,
                database_name=connector.project,
                schema_name=schema_name,
                table_type="full",
            )
            assert len(samples) == 1
            assert samples[0]["table_type"] == object_type
            assert "alpha" in samples[0]["sample_rows"]
    finally:
        _drop(connector, f"DROP MATERIALIZED VIEW IF EXISTS {full(mv_name)}", schema_name)
        _drop(connector, f"DROP VIEW IF EXISTS {full(view_name)}", schema_name)
        _drop(connector, f"DROP TABLE IF EXISTS {full(table_name)}", schema_name)
        _drop(connector, f"DROP TABLE IF EXISTS {full(scope_name)}", schema_name)
        _drop(connector, f"DROP TABLE IF EXISTS {full(scope_name, 'default')}")
        if schema_created:
            _drop(connector, f"DROP SCHEMA {schema_name}")


def test_three_level_partition_and_transactional_dml():
    connector = _connector(_required_env("MAXCOMPUTE_THREE_LEVEL_PROJECT"))
    prefix = f"datus_adapter_ci_{uuid.uuid4().hex[:12]}"
    partition_name = f"{prefix}_partitioned"
    transaction_name = f"{prefix}_transactional"
    partition_full = connector.full_name(
        database_name=connector.project,
        schema_name="default",
        table_name=partition_name,
    )
    transaction_full = connector.full_name(
        database_name=connector.project,
        schema_name="default",
        table_name=transaction_name,
    )

    try:
        _assert_success(
            connector.execute_ddl(
                f"CREATE TABLE {partition_full} (id BIGINT, label STRING) PARTITIONED BY (ds STRING) LIFECYCLE 1",
                database_name=connector.project,
                schema_name="default",
            ),
            "create partitioned table",
        )
        _assert_success(
            connector.execute_insert(
                f"INSERT OVERWRITE TABLE {partition_full} PARTITION (ds='20260828') SELECT 1, 'static'",
                database_name=connector.project,
                schema_name="default",
            ),
            "insert static partition",
        )
        _assert_success(
            connector.execute_insert(
                f"INSERT INTO TABLE {partition_full} PARTITION (ds) SELECT 2, 'dynamic', '20260829'",
                database_name=connector.project,
                schema_name="default",
            ),
            "insert dynamic partition",
        )
        partition_rows = _assert_success(
            connector.execute_query(
                f"SELECT id, label, ds FROM {partition_full} WHERE ds IN ('20260828', '20260829') ORDER BY id LIMIT 10",
                result_format="list",
                database_name=connector.project,
                schema_name="default",
            ),
            "query partitions",
        )
        assert partition_rows.sql_return == [
            {"id": 1, "label": "static", "ds": "20260828"},
            {"id": 2, "label": "dynamic", "ds": "20260829"},
        ]
        columns = connector.get_schema(
            database_name=connector.project,
            schema_name="default",
            table_name=partition_name,
        )
        assert next(column for column in columns if column["name"] == "ds")["is_partition"] is True

        _assert_success(
            connector.execute_ddl(
                f'CREATE TABLE {transaction_full} (id BIGINT, name STRING) TBLPROPERTIES ("transactional"="true")',
                database_name=connector.project,
                schema_name="default",
            ),
            "create transactional table",
        )
        _assert_success(
            connector.execute_insert(
                f"INSERT OVERWRITE TABLE {transaction_full} SELECT 1, 'one' UNION ALL SELECT 2, 'two'",
                database_name=connector.project,
                schema_name="default",
            ),
            "insert transactional rows",
        )
        _assert_success(
            connector.execute_update(
                f"UPDATE {transaction_full} SET name='updated' WHERE id=1",
                database_name=connector.project,
                schema_name="default",
            ),
            "update transactional row",
        )
        _assert_success(
            connector.execute_delete(
                f"DELETE FROM {transaction_full} WHERE id=2",
                database_name=connector.project,
                schema_name="default",
            ),
            "delete transactional row",
        )
        transaction_rows = _assert_success(
            connector.execute_query(
                f"SELECT id, name FROM {transaction_full} ORDER BY id LIMIT 10",
                result_format="list",
                database_name=connector.project,
                schema_name="default",
            ),
            "query transactional table",
        )
        assert transaction_rows.sql_return == [{"id": 1, "name": "updated"}]
    finally:
        _drop(connector, f"DROP TABLE IF EXISTS {transaction_full}")
        _drop(connector, f"DROP TABLE IF EXISTS {partition_full}")
