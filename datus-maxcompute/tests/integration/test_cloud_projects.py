# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import os
import uuid

import pytest

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
