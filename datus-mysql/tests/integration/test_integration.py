# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
import uuid

import pytest

from datus_mysql import MySQLConfig, MySQLConnector

# ==================== Connection Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: MySQLConfig):
    """Test connection using config object."""
    try:
        conn = MySQLConnector(config)
        assert conn.test_connection()
        conn.close()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")


@pytest.mark.integration
def test_connection_with_dict():
    """Test connection using dict config."""
    try:
        conn = MySQLConnector(
            {
                "host": os.getenv("MYSQL_HOST", "localhost"),
                "port": int(os.getenv("MYSQL_PORT", "3306")),
                "username": os.getenv("MYSQL_USER", "test_user"),
                "password": os.getenv("MYSQL_PASSWORD", "test_password"),
            }
        )
        assert conn.test_connection()
        conn.close()
    except Exception as e:
        pytest.skip(f"Database not available: {e}")


# ==================== Database Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases(connector: MySQLConnector):
    """Test getting list of databases."""
    databases = connector.get_databases()
    assert isinstance(databases, list)
    assert len(databases) > 0


@pytest.mark.integration
def test_get_databases_exclude_system(connector: MySQLConnector):
    """Test that system databases are excluded by default."""
    databases = connector.get_databases(include_sys=False)
    system_dbs = {"sys", "information_schema", "performance_schema", "mysql"}
    for db in databases:
        assert db not in system_dbs


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: MySQLConnector, config: MySQLConfig):
    """Test getting table list."""
    tables = connector.get_tables(database_name=config.database)
    assert isinstance(tables, list)


@pytest.mark.integration
def test_get_tables_with_ddl(connector: MySQLConnector, config: MySQLConfig):
    """Test getting tables with DDL."""
    # Create a test table first
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_table_{suffix}"

    connector.switch_context(database_name=config.database)
    create_result = connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )
    assert create_result.success, f"failed to create {table_name}: {create_result.error}"

    try:
        tables = connector.get_tables_with_ddl(database_name=config.database, tables=[table_name])

        # The `tables` filter is exact, so the table just created is the only expected result.
        assert [t["table_name"] for t in tables] == [table_name]

        table = tables[0]
        assert table["table_type"] == "table"
        assert table["catalog_name"] == ""
        assert table["database_name"] == config.database
        assert table["schema_name"] == ""
        assert table["identifier"] == f"{config.database}.{table_name}"

        definition = table["definition"]
        assert definition.startswith(f"CREATE TABLE `{table_name}` (")
        assert "`id` int" in definition
        assert "`name` varchar(50)" in definition
        assert "PRIMARY KEY (`id`)" in definition
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== View Tests ====================


@pytest.mark.integration
def test_get_views(connector: MySQLConnector, config: MySQLConfig):
    """Test getting view list."""
    views = connector.get_views(database_name=config.database)
    assert isinstance(views, list)


@pytest.mark.integration
def test_get_views_with_ddl(connector: MySQLConnector, config: MySQLConfig):
    """Test getting views with DDL."""
    # Create a test view first
    suffix = uuid.uuid4().hex[:8]
    view_name = f"test_view_{suffix}"
    table_name = f"test_table_{suffix}"

    connector.switch_context(database_name=config.database)

    # Create base table
    table_result = connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )
    assert table_result.success, f"failed to create {table_name}: {table_result.error}"

    # Create view
    view_result = connector.execute_ddl(f"CREATE VIEW {view_name} AS SELECT * FROM {table_name}")
    assert view_result.success, f"failed to create {view_name}: {view_result.error}"

    try:
        views = connector.get_views_with_ddl(database_name=config.database)

        matched = [v for v in views if v["table_name"] == view_name]
        assert len(matched) == 1, f"{view_name} missing from {[v['table_name'] for v in views]}"

        view = matched[0]
        assert view["table_type"] == "view"
        assert view["catalog_name"] == ""
        assert view["database_name"] == config.database
        assert view["schema_name"] == ""
        assert view["identifier"] == f"{config.database}.{view_name}"

        # SHOW CREATE VIEW prepends ALGORITHM/DEFINER/SQL SECURITY clauses, and only
        # qualifies the view with its database when it is not the session default.
        definition = view["definition"]
        assert definition.startswith("CREATE ")
        assert f"`{view_name}` AS " in definition
        assert f"`{table_name}`" in definition
    finally:
        connector.execute_ddl(f"DROP VIEW IF EXISTS {view_name}")
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Schema Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema(connector: MySQLConnector, config: MySQLConfig):
    """Test getting table schema."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_schema_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(50) NOT NULL,
            email VARCHAR(100),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    try:
        schema = connector.get_schema(database_name=config.database, table_name=table_name)

        assert len(schema) == 4

        # Check id column
        id_col = [col for col in schema if col["name"] == "id"][0]
        assert id_col["pk"] is True
        assert "int" in id_col["type"].lower()

        # Check name column
        name_col = [col for col in schema if col["name"] == "name"][0]
        assert name_col["nullable"] is False
        assert "varchar" in name_col["type"].lower()
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Sample Data Tests ====================


@pytest.mark.integration
def test_get_sample_rows(connector: MySQLConnector, config: MySQLConfig):
    """Test getting sample rows."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_sample_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )

    # Insert test data
    connector.execute_insert(
        f"""
        INSERT INTO {table_name} (id, name) VALUES
        (1, 'Alice'),
        (2, 'Bob'),
        (3, 'Charlie')
    """
    )

    try:
        sample_rows = connector.get_sample_rows(database_name=config.database, tables=[table_name], top_n=2)

        assert len(sample_rows) == 1
        assert sample_rows[0]["table_name"] == table_name
        assert "sample_rows" in sample_rows[0]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== SQL Execution Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select(connector: MySQLConnector):
    """Test executing SELECT query."""
    result = connector.execute({"sql_query": "SELECT 1 as num"}, result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_ddl(connector: MySQLConnector, config: MySQLConfig):
    """Test DDL operations."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_ddl_{suffix}"

    connector.switch_context(database_name=config.database)

    try:
        # CREATE
        create_result = connector.execute_ddl(
            f"""
            CREATE TABLE {table_name} (
                id INT PRIMARY KEY,
                name VARCHAR(50)
            )
        """
        )
        assert create_result.success

        # ALTER
        alter_result = connector.execute_ddl(f"ALTER TABLE {table_name} ADD COLUMN age INT")
        assert alter_result.success

    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_insert(connector: MySQLConnector, config: MySQLConfig):
    """Test INSERT operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_insert_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )

    try:
        insert_result = connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")
        assert insert_result.success
        assert insert_result.row_count == 2

        # Verify
        query_result = connector.execute(
            {"sql_query": f"SELECT id, name FROM {table_name} ORDER BY id"},
            result_format="list",
        )
        assert query_result.sql_return == [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_update(connector: MySQLConnector, config: MySQLConfig):
    """Test UPDATE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_update_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )

    try:
        # Insert initial data
        connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")

        # Update
        update_result = connector.execute_update(f"UPDATE {table_name} SET name = 'Alice Updated' WHERE id = 1")
        assert update_result.success
        assert update_result.row_count == 1

        # Verify
        query_result = connector.execute(
            {"sql_query": f"SELECT name FROM {table_name} WHERE id = 1"},
            result_format="list",
        )
        assert query_result.sql_return == [{"name": "Alice Updated"}]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


@pytest.mark.integration
def test_execute_delete(connector: MySQLConnector, config: MySQLConfig):
    """Test DELETE operation."""
    suffix = uuid.uuid4().hex[:8]
    table_name = f"test_delete_{suffix}"

    connector.switch_context(database_name=config.database)
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id INT PRIMARY KEY,
            name VARCHAR(50)
        )
    """
    )

    try:
        # Insert initial data
        connector.execute_insert(f"INSERT INTO {table_name} (id, name) VALUES (1, 'Alice'), (2, 'Bob')")

        # Delete
        delete_result = connector.execute_delete(f"DELETE FROM {table_name} WHERE id = 2")
        assert delete_result.success
        assert delete_result.row_count == 1

        # Verify
        query_result = connector.execute({"sql_query": f"SELECT id FROM {table_name}"}, result_format="list")
        assert query_result.sql_return == [{"id": 1}]
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Error Handling Tests ====================


@pytest.mark.integration
def test_exception_on_syntax_error(connector: MySQLConnector):
    """Test that syntax error returns error result."""
    result = connector.execute({"sql_query": "INVALID SQL SYNTAX"})
    assert result.error is not None or not result.success


@pytest.mark.integration
def test_exception_on_nonexistent_table(connector: MySQLConnector):
    """Test that non-existent table returns error result."""
    result = connector.execute({"sql_query": f"SELECT * FROM nonexistent_table_{uuid.uuid4().hex}"})
    assert result.error is not None or not result.success


# ==================== Utility Tests ====================


@pytest.mark.integration
def test_full_name_with_database(connector: MySQLConnector):
    """Test full_name with database."""
    full_name = connector.full_name(database_name="mydb", table_name="mytable")
    assert full_name == "`mydb`.`mytable`"


@pytest.mark.integration
def test_full_name_without_database(connector: MySQLConnector):
    """Test full_name without database."""
    full_name = connector.full_name(table_name="mytable")
    assert full_name == "`mytable`"


@pytest.mark.integration
def test_identifier(connector: MySQLConnector):
    """Test identifier generation."""
    identifier = connector.identifier(database_name="mydb", table_name="mytable")
    assert identifier == "mydb.mytable"
