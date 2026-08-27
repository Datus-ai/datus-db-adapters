# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
import uuid

import pytest

from datus_oracle import OracleConfig, OracleConnector

from .conftest import drop_table_sql

# ==================== Connection Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: OracleConfig):
    """Test connection using config object (SELECT 1 FROM DUAL)."""
    conn = None
    try:
        conn = OracleConnector(config)
        assert conn.test_connection()
    finally:
        if conn is not None:
            conn.close()


@pytest.mark.integration
def test_connection_with_dict():
    """Test connection using dict config with the database compatibility alias."""
    conn = None
    try:
        conn = OracleConnector(
            {
                "host": os.getenv("ORACLE_HOST", "localhost"),
                "port": int(os.getenv("ORACLE_PORT", "1521")),
                "username": os.getenv("ORACLE_USER", "datus_test"),
                "password": os.getenv("ORACLE_PASSWORD", "test_password"),
                "database": os.getenv("ORACLE_SERVICE_NAME", "FREEPDB1"),
            }
        )
        assert conn.test_connection()
    finally:
        if conn is not None:
            conn.close()


# ==================== Namespace Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases_is_empty(connector: OracleConnector):
    """Oracle namespace is schema-only; the service/PDB is a connection target."""
    assert connector.get_databases() == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schemas(connector: OracleConnector, config: OracleConfig):
    """Test getting list of schemas from ALL_USERS."""
    schemas = connector.get_schemas()
    assert isinstance(schemas, list)
    assert config.schema_name in schemas


@pytest.mark.integration
def test_get_schemas_exclude_system(connector: OracleConnector):
    """Test that Oracle-maintained schemas are excluded by default."""
    schemas = connector.get_schemas(include_sys=False)
    for schema in schemas:
        assert schema not in {"SYS", "SYSTEM", "XDB", "CTXSYS"}


# ==================== Table Metadata Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables(connector: OracleConnector, config: OracleConfig):
    """Test getting table list."""
    tables = connector.get_tables(schema_name=config.schema_name)
    assert isinstance(tables, list)


@pytest.mark.integration
def test_get_tables_with_ddl(connector: OracleConnector, config: OracleConfig):
    """Test getting tables with DDL via DBMS_METADATA.GET_DDL."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_TABLE_{suffix}"
    table_ref = connector.full_name(table_name=table_name)

    connector.execute_ddl(
        f"""
        CREATE TABLE {table_ref} (
            "ID" NUMBER(10) PRIMARY KEY,
            "NAME" VARCHAR2(50)
        )
    """
    )

    try:
        tables = connector.get_tables_with_ddl(schema_name=config.schema_name, tables=[table_name])

        assert len(tables) == 1
        table = tables[0]
        assert table["table_name"] == table_name
        assert table["table_type"] == "table"
        assert table["schema_name"] == config.schema_name
        assert "CREATE TABLE" in table["definition"]
        assert table_name in table["definition"]
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== View Tests ====================


@pytest.mark.integration
def test_get_views_with_ddl(connector: OracleConnector, config: OracleConfig):
    """Test getting views with DDL."""
    suffix = uuid.uuid4().hex[:8].upper()
    view_name = f"TEST_VIEW_{suffix}"
    table_name = f"TEST_TABLE_{suffix}"
    table_ref = connector.full_name(table_name=table_name)
    view_ref = connector.full_name(table_name=view_name)

    connector.execute_ddl(f'CREATE TABLE {table_ref} ("ID" NUMBER(10) PRIMARY KEY, "NAME" VARCHAR2(50))')

    try:
        connector.execute_ddl(f"CREATE VIEW {view_ref} AS SELECT * FROM {table_ref}")

        views = connector.get_views(schema_name=config.schema_name)
        assert view_name in views

        views_ddl = connector.get_views_with_ddl(schema_name=config.schema_name)
        view = [v for v in views_ddl if v["table_name"] == view_name]
        assert len(view) == 1
        assert view[0]["table_type"] == "view"
        assert "CREATE" in view[0]["definition"]
    finally:
        connector.execute_ddl(f"DROP VIEW {view_ref}")
        connector.execute_ddl(drop_table_sql(table_ref))


@pytest.mark.integration
def test_get_materialized_views_with_data(connector: OracleConnector, config: OracleConfig):
    """Test materialized view listing."""
    suffix = uuid.uuid4().hex[:8].upper()
    mv_name = f"TEST_MV_{suffix}"
    table_name = f"TEST_TABLE_{suffix}"
    table_ref = connector.full_name(table_name=table_name)
    mv_ref = connector.full_name(table_name=mv_name)

    connector.execute_ddl(f'CREATE TABLE {table_ref} ("ID" NUMBER(10) PRIMARY KEY)')

    try:
        create_result = connector.execute_ddl(f"CREATE MATERIALIZED VIEW {mv_ref} AS SELECT * FROM {table_ref}")
        assert create_result.success, create_result.error

        mvs = connector.get_materialized_views(schema_name=config.schema_name)
        assert mv_name in mvs
    finally:
        connector.execute_ddl(f"DROP MATERIALIZED VIEW {mv_ref}")
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== Column Schema Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema(connector: OracleConnector, config: OracleConfig):
    """Test getting table schema: types, PK, nullability, default, comment."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_SCHEMA_{suffix}"
    table_ref = connector.full_name(table_name=table_name)

    connector.execute_ddl(
        f"""
        CREATE TABLE {table_ref} (
            "ID" NUMBER(10) PRIMARY KEY,
            "NAME" VARCHAR2(50) NOT NULL,
            "EMAIL" VARCHAR2(100),
            "BALANCE" NUMBER(15,2),
            "CREATED_AT" TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    connector.execute_ddl(f"COMMENT ON COLUMN {table_ref}.\"EMAIL\" IS 'contact email'")

    try:
        schema = connector.get_schema(schema_name=config.schema_name, table_name=table_name)

        assert len(schema) == 5

        id_col = [col for col in schema if col["name"] == "ID"][0]
        assert id_col["pk"] is True
        assert id_col["type"] == "NUMBER(10)"
        assert id_col["nullable"] is False

        name_col = [col for col in schema if col["name"] == "NAME"][0]
        assert name_col["nullable"] is False
        assert name_col["type"] == "VARCHAR2(50)"

        balance_col = [col for col in schema if col["name"] == "BALANCE"][0]
        assert balance_col["type"] == "NUMBER(15,2)"

        email_col = [col for col in schema if col["name"] == "EMAIL"][0]
        assert email_col["comment"] == "contact email"
        assert email_col["nullable"] is True

        created_col = [col for col in schema if col["name"] == "CREATED_AT"][0]
        assert created_col["default_value"] is not None
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== Sample Data Tests ====================


@pytest.mark.integration
def test_get_sample_rows(connector: OracleConnector, config: OracleConfig):
    """Test getting sample rows (FETCH FIRST under the hood)."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_SAMPLE_{suffix}"
    table_ref = connector.full_name(table_name=table_name)

    connector.execute_ddl(f'CREATE TABLE {table_ref} ("ID" NUMBER(10) PRIMARY KEY, "NAME" VARCHAR2(50))')

    try:
        for i, name in enumerate(("Alice", "Bob", "Charlie"), start=1):
            connector.execute_insert(f"INSERT INTO {table_ref} VALUES ({i}, '{name}')")

        sample_rows = connector.get_sample_rows(schema_name=config.schema_name, tables=[table_name], top_n=2)

        assert len(sample_rows) == 1
        assert sample_rows[0]["table_name"] == table_name
        # top_n=2 rows + header line
        csv_lines = sample_rows[0]["sample_rows"].strip().splitlines()
        assert len(csv_lines) == 3
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== SQL Execution Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_select(connector: OracleConnector):
    """Test executing SELECT query against DUAL."""
    result = connector.execute({"sql_query": "SELECT 1 AS num FROM DUAL"}, result_format="list")
    assert result.success
    assert not result.error
    assert result.sql_return == [{"num": 1}]


@pytest.mark.integration
@pytest.mark.acceptance
def test_execute_ddl_and_dml(connector: OracleConnector):
    """Test DDL + INSERT/UPDATE/DELETE with row counts."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_DML_{suffix}"
    table_ref = connector.full_name(table_name=table_name)

    try:
        create_result = connector.execute_ddl(
            f'CREATE TABLE {table_ref} ("ID" NUMBER(10) PRIMARY KEY, "NAME" VARCHAR2(50))'
        )
        assert create_result.success

        alter_result = connector.execute_ddl(f'ALTER TABLE {table_ref} ADD "AGE" NUMBER(3)')
        assert alter_result.success

        insert_result = connector.execute_insert(f'INSERT INTO {table_ref} ("ID", "NAME") VALUES (1, \'Alice\')')
        assert insert_result.success
        assert insert_result.row_count == 1
        connector.execute_insert(f'INSERT INTO {table_ref} ("ID", "NAME") VALUES (2, \'Bob\')')

        update_result = connector.execute_update(f'UPDATE {table_ref} SET "NAME" = \'Alice Updated\' WHERE "ID" = 1')
        assert update_result.success
        assert update_result.row_count == 1

        delete_result = connector.execute_delete(f'DELETE FROM {table_ref} WHERE "ID" = 2')
        assert delete_result.success
        assert delete_result.row_count == 1

        query_result = connector.execute({"sql_query": f"SELECT id, name FROM {table_ref}"}, result_format="list")
        assert query_result.sql_return == [{"id": 1, "name": "Alice Updated"}]
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


@pytest.mark.integration
def test_transaction_rollback_on_error(connector: OracleConnector):
    """A failed statement must not poison subsequent statements."""
    bad = connector.execute({"sql_query": "SELECT * FROM nonexistent_table_xyz"})
    assert not bad.success

    good = connector.execute({"sql_query": "SELECT 1 AS num FROM DUAL"}, result_format="list")
    assert good.success
    assert good.sql_return == [{"num": 1}]


# ==================== Result Format Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_result_formats(connector: OracleConnector):
    """List, CSV, Pandas and Arrow result formats."""
    sql = "SELECT 1 AS id, 'x' AS label FROM DUAL"

    as_list = connector.execute({"sql_query": sql}, result_format="list")
    assert as_list.success
    assert as_list.sql_return == [{"id": 1, "label": "x"}]

    as_csv = connector.execute({"sql_query": sql}, result_format="csv")
    assert as_csv.success
    assert "id" in as_csv.sql_return.splitlines()[0]

    as_pandas = connector.execute({"sql_query": sql}, result_format="pandas")
    assert as_pandas.success
    assert len(as_pandas.sql_return) == 1

    as_arrow = connector.execute({"sql_query": sql}, result_format="arrow")
    assert as_arrow.success
    assert as_arrow.sql_return.num_rows == 1


# ==================== Type Round-trip Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_type_roundtrip(connector: OracleConnector):
    """NUMBER, DATE, TIMESTAMP, CLOB and BLOB values survive a round-trip."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_TYPES_{suffix}"
    table_ref = connector.full_name(table_name=table_name)

    connector.execute_ddl(
        f"""
        CREATE TABLE {table_ref} (
            "ID" NUMBER(10) PRIMARY KEY,
            "AMOUNT" NUMBER(15,2),
            "RATIO" BINARY_DOUBLE,
            "EVENT_DATE" DATE,
            "EVENT_TS" TIMESTAMP,
            "NOTES" CLOB,
            "PAYLOAD" BLOB
        )
    """
    )

    try:
        insert_result = connector.execute_insert(
            f"""
            INSERT INTO {table_ref} VALUES (
                1,
                12345.67,
                0.5,
                DATE '2026-01-02',
                TIMESTAMP '2026-01-02 03:04:05',
                'a clob note',
                HEXTORAW('DEADBEEF')
            )
        """
        )
        assert insert_result.success, insert_result.error

        result = connector.execute({"sql_query": f"SELECT * FROM {table_ref}"}, result_format="list")
        assert result.success, result.error
        row = result.sql_return[0]
        assert row["id"] == 1
        assert float(row["amount"]) == 12345.67
        assert float(row["ratio"]) == 0.5
        assert "2026-01-02" in str(row["event_date"])
        assert "03:04:05" in str(row["event_ts"])
        assert "clob note" in str(row["notes"])
        assert row["payload"] is not None
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== Identifier Case Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_mixed_case_source_columns_reachable_unquoted(connector: OracleConnector):
    """Upper-case + double-quote policy: quoted DDL stays reachable from bare SQL."""
    suffix = uuid.uuid4().hex[:8].upper()
    table_name = f"TEST_CASE_{suffix}"
    q = connector.quote_identifier
    table_ref = connector.full_name(table_name=table_name)

    # Mixed-case source names are folded to upper case by quote_identifier
    connector.execute_ddl(f"CREATE TABLE {table_ref} ({q('OrderId')} NUMBER(10), {q('order')} VARCHAR2(20))")

    try:
        connector.execute_insert(f"INSERT INTO {table_ref} VALUES (1, 'first')")

        # Bare (unquoted) SQL written later must reach the same columns
        result = connector.execute(
            {"sql_query": f'SELECT orderid, "ORDER" FROM {table_ref}'},
            result_format="list",
        )
        assert result.success, result.error
        assert result.sql_return[0]["orderid"] == 1
    finally:
        connector.execute_ddl(drop_table_sql(table_ref))


# ==================== Error Handling Tests ====================


@pytest.mark.integration
def test_exception_on_syntax_error(connector: OracleConnector):
    result = connector.execute({"sql_query": "SELECT * FROM WHERE"})
    assert result.error is not None or not result.success


@pytest.mark.integration
@pytest.mark.acceptance
def test_missing_table_error_preserves_ora_text(connector: OracleConnector):
    """ORA-00942 mapping must keep the driver's English text (Agent contract)."""
    result = connector.execute({"sql_query": f"SELECT * FROM missing_{uuid.uuid4().hex[:8]}"})
    assert not result.success
    error_text = str(result.error).lower()
    assert "does not exist" in error_text
    assert "table" in error_text


# ==================== Utility Tests ====================


@pytest.mark.integration
def test_full_name_and_identifier(connector: OracleConnector, config: OracleConfig):
    assert connector.full_name(table_name="mytable") == f'"{config.schema_name}"."MYTABLE"'
    assert connector.identifier(table_name="MYTABLE") == f"{config.schema_name}.MYTABLE"
