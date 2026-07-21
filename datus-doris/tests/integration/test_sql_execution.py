# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_doris import DorisConfig, DorisConnector

# ==================== Query Execution Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select_query(connector: DorisConnector):
    """Test executing simple SELECT query."""
    result = connector.execute({"sql_query": "SELECT 1 as num"}, result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
def test_execute_explain_query(connector: DorisConnector, config: DorisConfig):
    """Test executing EXPLAIN query."""
    tables = connector.get_tables(catalog_name=config.catalog, database_name=config.database)

    if len(tables) > 0:
        table_name = tables[0]
        full_name = connector.full_name(
            catalog_name=config.catalog,
            database_name=config.database,
            table_name=table_name,
        )

        result = connector.execute({"sql_query": f"EXPLAIN SELECT * FROM {full_name} LIMIT 1"})
        assert result.success
        assert not result.error
        assert result.sql_return
    else:
        pytest.skip("No tables available for EXPLAIN test")


# ==================== DDL Operation Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_ddl_create_drop(connector: DorisConnector, config: DorisConfig):
    """Test DDL operations (CREATE/DROP table)."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    UNIQUE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1",
        "enable_unique_key_merge_on_write" = "true"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        assert create_result.success, f"Failed to create table: {create_result.error}"
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_ddl_create_materialized_view(connector: DorisConnector, config: DorisConfig):
    """Test creating an independently queryable async materialized view."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_base_{suffix}"
    mv_name = f"datus_mv_{suffix}"

    connector.switch_context(database_name=config.database)

    # Create base table first
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `id` BIGINT NOT NULL,
        `value` INT
    ) ENGINE=OLAP
    UNIQUE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES ("replication_num" = "1",
        "enable_unique_key_merge_on_write" = "true");
    """

    try:
        create_result = connector.execute_ddl(create_table_sql)
        assert create_result.success, create_result.error

        create_mv_sql = f"""
        CREATE MATERIALIZED VIEW {mv_name}
        BUILD IMMEDIATE REFRESH COMPLETE ON MANUAL
        DISTRIBUTED BY HASH(id) BUCKETS 1
        PROPERTIES ("replication_num" = "1")
        AS SELECT id, SUM(value) as total
        FROM {table_name}
        GROUP BY id;
        """

        mv_result = connector.execute_ddl(create_mv_sql)
        assert mv_result.success, mv_result.error
        assert mv_name in connector.get_materialized_views(database_name=config.database)
    finally:
        connector.execute_ddl(f"DROP MATERIALIZED VIEW IF EXISTS {mv_name}")
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== DML Operation Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_insert(connector: DorisConnector, config: DorisConfig):
    """Test INSERT operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_insert_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    UNIQUE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1",
        "enable_unique_key_merge_on_write" = "true"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        if not create_result.success:
            pytest.skip(f"Unable to create test table: {create_result.error}")

        # Insert data
        insert_result = connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
        assert insert_result.success

        # Verify
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
def test_execute_update(connector: DorisConnector, config: DorisConfig):
    """Test UPDATE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_update_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    UNIQUE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1",
        "enable_unique_key_merge_on_write" = "true"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        if not create_result.success:
            pytest.skip(f"Unable to create test table: {create_result.error}")

        # Insert initial data
        connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")

        # Update
        update_result = connector.execute(
            {"sql_query": f"UPDATE {table_name} SET name = 'Alice Updated' WHERE id = 1"},
            result_format="list",
        )
        assert update_result.success

        # Verify
        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.sql_return == [
            {"id": 1, "name": "Alice Updated"},
            {"id": 2, "name": "Bob"},
        ]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_delete(connector: DorisConnector, config: DorisConfig):
    """Test DELETE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"datus_delete_test_{suffix}"

    connector.switch_context(database_name=config.database)

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        `id` BIGINT NOT NULL,
        `name` VARCHAR(64)
    ) ENGINE=OLAP
    UNIQUE KEY (`id`)
    DISTRIBUTED BY HASH(`id`) BUCKETS 1
    PROPERTIES (
        "replication_num" = "1",
        "enable_unique_key_merge_on_write" = "true"
    );
    """

    try:
        create_result = connector.execute_ddl(create_sql)
        if not create_result.success:
            pytest.skip(f"Unable to create test table: {create_result.error}")

        # Insert initial data
        connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")

        # Delete
        delete_result = connector.execute(
            {"sql_query": f"DELETE FROM {table_name} WHERE id = 2"},
            result_format="list",
        )
        assert delete_result.success

        # Verify
        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.sql_return == [{"id": 1, "name": "Alice"}]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Error Handling Tests ====================


@pytest.mark.integration
def test_execute_error_handling(connector: DorisConnector):
    """Test SQL error handling."""
    # Execute query on non-existent table
    result = connector.execute({"sql_query": "SELECT * FROM nonexistent_table_12345"})

    # Should return error (not raise exception)
    assert not result.success or result.error
