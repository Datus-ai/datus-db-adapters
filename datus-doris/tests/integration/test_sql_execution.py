# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_doris import DorisConfig, DorisConnector

METADATA_TABLE = "datus_metadata_table"


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select(connector: DorisConnector):
    result = connector.execute(
        {"sql_query": "SELECT 1 AS num"},
        result_format="list",
    )

    assert result.success
    assert result.error is None
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
def test_execute_explain(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    table = connector.full_name(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=METADATA_TABLE,
    )

    result = connector.execute({"sql_query": f"EXPLAIN SELECT * FROM {table} LIMIT 1"})

    assert result.success
    assert result.sql_return


@pytest.mark.integration
def test_create_async_materialized_view(
    connector: DorisConnector,
    config: DorisConfig,
):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_base_{suffix}"
    mv_name = f"datus_mv_{suffix}"
    connector.switch_context(database_name=config.database)

    try:
        create_table = connector.execute_ddl(
            f"""
            CREATE TABLE `{table_name}` (
                `id` BIGINT NOT NULL,
                `value` INT
            ) ENGINE=OLAP
            UNIQUE KEY (`id`)
            DISTRIBUTED BY HASH(`id`) BUCKETS 1
            PROPERTIES (
                "replication_num" = "1",
                "enable_unique_key_merge_on_write" = "true"
            )
            """
        )
        assert create_table.success, create_table.error

        create_mv = connector.execute_ddl(
            f"""
            CREATE MATERIALIZED VIEW `{mv_name}`
            BUILD IMMEDIATE REFRESH COMPLETE ON MANUAL
            DISTRIBUTED BY HASH(id) BUCKETS 1
            PROPERTIES ("replication_num" = "1")
            AS SELECT id, SUM(value) AS total
            FROM `{table_name}`
            GROUP BY id
            """
        )
        assert create_mv.success, create_mv.error
        assert mv_name in connector.get_materialized_views(database_name=config.database)
    finally:
        connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS `{mv_name}`")
        connector.execute_ddl(f"DROP TABLE IF EXISTS `{table_name}`")


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_insert(
    connector: DorisConnector,
    unique_key_table: str,
):
    result = connector.execute_insert(f"INSERT INTO `{unique_key_table}` (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
    assert result.success, result.error

    query = connector.execute(
        {"sql_query": (f"SELECT id, name FROM `{unique_key_table}` ORDER BY id")},
        result_format="list",
    )
    assert query.sql_return == [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
    ]


@pytest.mark.integration
def test_execute_update(
    connector: DorisConnector,
    unique_key_table: str,
):
    insert = connector.execute_insert(f"INSERT INTO `{unique_key_table}` (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
    assert insert.success, insert.error

    update = connector.execute(
        {"sql_query": (f"UPDATE `{unique_key_table}` SET name = 'Alice Updated' WHERE id = 1")},
        result_format="list",
    )
    assert update.success, update.error

    query = connector.execute(
        {"sql_query": (f"SELECT id, name FROM `{unique_key_table}` ORDER BY id")},
        result_format="list",
    )
    assert query.sql_return == [
        {"id": 1, "name": "Alice Updated"},
        {"id": 2, "name": "Bob"},
    ]


@pytest.mark.integration
def test_execute_delete(
    connector: DorisConnector,
    unique_key_table: str,
):
    insert = connector.execute_insert(f"INSERT INTO `{unique_key_table}` (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
    assert insert.success, insert.error

    delete = connector.execute(
        {"sql_query": (f"DELETE FROM `{unique_key_table}` WHERE id = 2")},
        result_format="list",
    )
    assert delete.success, delete.error

    query = connector.execute(
        {"sql_query": (f"SELECT id, name FROM `{unique_key_table}` ORDER BY id")},
        result_format="list",
    )
    assert query.sql_return == [{"id": 1, "name": "Alice"}]


@pytest.mark.integration
def test_execute_returns_sql_errors(connector: DorisConnector):
    result = connector.execute({"sql_query": "SELECT * FROM nonexistent_table_12345"})

    assert result.success is False
    assert result.error
