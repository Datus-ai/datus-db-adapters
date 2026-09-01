# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import time
import uuid

import pytest

from datus_starrocks import StarRocksConfig, StarRocksConnector

METADATA_TABLE = "datus_metadata_table"

MV_VISIBLE_TIMEOUT_SECONDS = 90

# ==================== Query Execution Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select_query(connector: StarRocksConnector):
    """Test executing simple SELECT query."""
    result = connector.execute({"sql_query": "SELECT 1 as num"}, result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
def test_execute_explain_query(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """EXPLAIN runs against a catalog-qualified table and returns a plan."""
    full_name = connector.full_name(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name=METADATA_TABLE,
    )

    result = connector.execute({"sql_query": f"EXPLAIN SELECT * FROM {full_name} LIMIT 1"})
    assert result.success, f"EXPLAIN failed: {result.error}"
    assert not result.error
    assert result.sql_return


# ==================== DDL Operation Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_ddl_create_drop(connector: StarRocksConnector, config: StarRocksConfig):
    """A created table becomes listable, and stops being listable once dropped."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_test_{suffix}"
    scope = {"catalog_name": config.catalog, "database_name": config.database}

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    PRIMARY KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        assert create_result.success, f"Failed to create table: {create_result.error}"
        assert table_name in connector.get_tables(**scope)

        drop_result = connector.execute_ddl(f"DROP TABLE {table_name}")
        assert drop_result.success, f"Failed to drop table: {drop_result.error}"
        assert table_name not in connector.get_tables(**scope)
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_ddl_create_drop_materialized_view(connector: StarRocksConnector, config: StarRocksConfig):
    """An asynchronous materialized view is created, listed, and dropped.

    The REFRESH clause is what selects the asynchronous form. Without it
    StarRocks builds a synchronous rollup instead, which no listing returns and
    which a PRIMARY KEY base table rejects outright — hence the DUPLICATE KEY
    base table and the explicit refresh scheme.
    """
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_base_{suffix}"
    mv_name = f"datus_mv_{suffix}"
    scope = {"catalog_name": config.catalog, "database_name": config.database}

    connector.switch_context(database_name=config.database)

    create_table_sql = f"""
    CREATE TABLE {table_name} (
        `id` BIGINT NOT NULL,
        `value` INT
    ) ENGINE=OLAP
    DUPLICATE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES ("replication_num" = "1");
    """

    create_mv_sql = f"""
    CREATE MATERIALIZED VIEW {mv_name}
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    REFRESH ASYNC
    PROPERTIES ("replication_num" = "1")
    AS SELECT `id`, SUM(`value`) AS total_value
    FROM {table_name}
    GROUP BY `id`;
    """

    try:
        create_result = connector.execute_ddl(create_table_sql)
        assert create_result.success, f"Failed to create base table: {create_result.error}"

        insert_result = connector.execute_insert(f"INSERT INTO {table_name} VALUES (1, 10), (2, 20)")
        assert insert_result.success, f"Failed to insert base rows: {insert_result.error}"

        mv_result = connector.execute_ddl(create_mv_sql)
        assert mv_result.success, f"Failed to create materialized view: {mv_result.error}"
        _wait_until_materialized_view_is_listed(connector, config, mv_name)

        drop_result = connector.execute_ddl(f"DROP MATERIALIZED VIEW {mv_name}")
        assert drop_result.success, f"Failed to drop materialized view: {drop_result.error}"
        assert mv_name not in connector.get_materialized_views(**scope)
    finally:
        connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS {mv_name}")
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


def _wait_until_materialized_view_is_listed(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    mv_name: str,
) -> None:
    """Block until the materialized view is listable.

    ``CREATE MATERIALIZED VIEW`` returns once the definition is accepted; the FE
    publishes it to ``information_schema`` on its own schedule.
    """
    deadline = time.monotonic() + MV_VISIBLE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        listed = connector.get_materialized_views(
            catalog_name=config.catalog,
            database_name=config.database,
        )
        if mv_name in listed:
            return
        time.sleep(2)
    raise AssertionError(f"Materialized view {mv_name} was not listed within {MV_VISIBLE_TIMEOUT_SECONDS} seconds")


# ==================== DML Operation Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_insert(connector: StarRocksConnector, config: StarRocksConfig):
    """Test INSERT operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_insert_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    PRIMARY KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        assert create_result.success, f"Failed to create test table: {create_result.error}"

        insert_result = connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
        assert insert_result.success, f"Failed to insert rows: {insert_result.error}"

        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.success
        assert query_result.sql_return == [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_update(connector: StarRocksConnector, config: StarRocksConfig):
    """Test UPDATE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_update_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    PRIMARY KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        assert create_result.success, f"Failed to create test table: {create_result.error}"

        insert_result = connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
        assert insert_result.success, f"Failed to insert rows: {insert_result.error}"

        update_result = connector.execute(
            {"sql_query": f"UPDATE {table_name} SET name = 'Alice Updated' WHERE id = 1"},
            result_format="list",
        )
        assert update_result.success, f"Failed to update row: {update_result.error}"

        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.success
        assert query_result.sql_return == [
            {"id": 1, "name": "Alice Updated"},
            {"id": 2, "name": "Bob"},
        ]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_delete(connector: StarRocksConnector, config: StarRocksConfig):
    """Test DELETE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_delete_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    PRIMARY KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        assert create_result.success, f"Failed to create test table: {create_result.error}"

        insert_result = connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
        assert insert_result.success, f"Failed to insert rows: {insert_result.error}"

        delete_result = connector.execute(
            {"sql_query": f"DELETE FROM {table_name} WHERE id = 2"},
            result_format="list",
        )
        assert delete_result.success, f"Failed to delete row: {delete_result.error}"

        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.success
        assert query_result.sql_return == [{"id": 1, "name": "Alice"}]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Error Handling Tests ====================


@pytest.mark.integration
def test_execute_error_handling(connector: StarRocksConnector):
    """A failing statement is reported, not raised."""
    result = connector.execute({"sql_query": "SELECT * FROM nonexistent_table_12345"})

    assert result.success is False
    assert result.error
