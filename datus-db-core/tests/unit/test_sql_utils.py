# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for sql_utils module."""

import pytest

from datus_db_core.constants import SQLType
from datus_db_core.registry import ConnectorRegistry
from datus_db_core.sql_utils import (
    _first_statement,
    mask_sql_quoted_regions,
    metadata_identifier,
    parse_context_switch,
    parse_dialect,
    parse_read_dialect,
    parse_sql_type,
    strip_sql_comments,
)


@pytest.fixture(autouse=True)
def restore_registry_metadata():
    saved_connectors = ConnectorRegistry._connectors.copy()
    saved_factories = ConnectorRegistry._factories.copy()
    saved_metadata = ConnectorRegistry._metadata.copy()
    saved_capabilities = ConnectorRegistry._capabilities.copy()
    saved_uri_builders = ConnectorRegistry._uri_builders.copy()
    saved_context_resolvers = ConnectorRegistry._context_resolvers.copy()
    yield
    ConnectorRegistry._connectors = saved_connectors
    ConnectorRegistry._factories = saved_factories
    ConnectorRegistry._metadata = saved_metadata
    ConnectorRegistry._capabilities = saved_capabilities
    ConnectorRegistry._uri_builders = saved_uri_builders
    ConnectorRegistry._context_resolvers = saved_context_resolvers


class TestParseReadDialect:
    def test_postgres_variants(self):
        assert parse_read_dialect("postgres") == "postgres"
        assert parse_read_dialect("postgresql") == "postgres"
        assert parse_read_dialect("redshift") == "postgres"
        assert parse_read_dialect("greenplum") == "postgres"

    def test_hive_variants(self):
        assert parse_read_dialect("spark") == "hive"
        assert parse_read_dialect("databricks") == "hive"
        assert parse_read_dialect("hive") == "hive"
        assert parse_read_dialect("starrocks") == "hive"

    def test_mssql_variants(self):
        assert parse_read_dialect("mssql") == "tsql"
        assert parse_read_dialect("sqlserver") == "tsql"

    def test_passthrough(self):
        assert parse_read_dialect("snowflake") == "snowflake"
        assert parse_read_dialect("mysql") == "mysql"

    def test_empty_and_none(self):
        assert parse_read_dialect("") == ""
        assert parse_read_dialect(None) == ""

    def test_registered_parser_dialect(self):
        ConnectorRegistry.register("customdb", object, parser_dialect="hive")
        assert parse_read_dialect("customdb") == "hive"


class TestParseDialect:
    def test_postgres_variants(self):
        assert parse_dialect("postgres") == "postgres"
        assert parse_dialect("postgresql") == "postgres"

    def test_mssql_variants(self):
        assert parse_dialect("mssql") == "tsql"
        assert parse_dialect("sqlserver") == "tsql"

    def test_passthrough(self):
        assert parse_dialect("snowflake") == "snowflake"
        assert parse_dialect("mysql") == "mysql"

    def test_registered_parser_dialect(self):
        ConnectorRegistry.register("customdb", object, parser_dialect="hive")
        assert parse_dialect("customdb") == "hive"


class TestStripSqlComments:
    def test_block_comment(self):
        assert strip_sql_comments("SELECT /* comment */ 1").strip() == "SELECT   1"

    def test_line_comment(self):
        result = strip_sql_comments("SELECT 1 -- comment\nFROM t")
        assert "comment" not in result
        assert "FROM t" in result

    def test_no_comments(self):
        assert strip_sql_comments("SELECT 1") == "SELECT 1"

    def test_multiline_block_comment(self):
        sql = "SELECT /* this\nis\nmultiline */ 1"
        result = strip_sql_comments(sql)
        assert "multiline" not in result
        assert "1" in result

    def test_comment_markers_inside_backtick_identifier(self):
        sql = "SELECT `dash--name`, `block/*name*/` -- real comment\nFROM t"
        result = strip_sql_comments(sql)
        assert "`dash--name`" in result
        assert "`block/*name*/`" in result
        assert "real comment" not in result
        assert "FROM t" in result


class TestMaskSqlQuotedRegions:
    def test_masks_literals_and_quoted_identifiers(self):
        sql = "CREATE TABLE `FULLTEXT` (note VARCHAR DEFAULT 'CHECK(id)') DUPLICATE KEY(id)"
        masked = mask_sql_quoted_regions(sql)

        assert len(masked) == len(sql)
        assert "FULLTEXT" not in masked
        assert "CHECK" not in masked
        assert "DUPLICATE KEY" in masked


class TestFirstStatement:
    def test_single_statement(self):
        assert _first_statement("SELECT 1") == "SELECT 1"

    def test_multiple_statements(self):
        assert _first_statement("SELECT 1; SELECT 2") == "SELECT 1"

    def test_semicolon_in_string(self):
        result = _first_statement("SELECT 'a;b' FROM t")
        assert result == "SELECT 'a;b' FROM t"

    def test_empty_string(self):
        assert _first_statement("") == ""

    def test_whitespace_only(self):
        assert _first_statement("   ") == ""

    def test_with_comments(self):
        result = _first_statement("/* comment */ SELECT 1; SELECT 2")
        assert result == "SELECT 1"

    def test_semicolon_in_backticks(self):
        result = _first_statement("SELECT `col;name` FROM t")
        assert result == "SELECT `col;name` FROM t"

    def test_semicolon_in_brackets(self):
        result = _first_statement("SELECT [col;name] FROM t")
        assert result == "SELECT [col;name] FROM t"

    def test_semicolon_in_double_quotes(self):
        result = _first_statement('SELECT "col;name" FROM t')
        assert result == 'SELECT "col;name" FROM t'

    def test_semicolon_in_dollar_quoted_string(self):
        result = _first_statement("SELECT $body$hello;world$body$ FROM t")
        assert result == "SELECT $body$hello;world$body$ FROM t"

    def test_semicolon_in_anonymous_dollar_quote(self):
        result = _first_statement("SELECT $$hello;world$$ FROM t")
        assert result == "SELECT $$hello;world$$ FROM t"

    def test_escaped_single_quote_before_semicolon(self):
        result = _first_statement("SELECT 'it''s;here' FROM t")
        assert result == "SELECT 'it''s;here' FROM t"

    def test_escaped_double_quote_before_semicolon(self):
        result = _first_statement('SELECT "col""name;x" FROM t')
        assert result == 'SELECT "col""name;x" FROM t'

    def test_escaped_backtick_before_semicolon(self):
        result = _first_statement("SELECT `col``name;x` FROM t")
        assert result == "SELECT `col``name;x` FROM t"

    def test_semicolon_after_closing_quote(self):
        result = _first_statement("SELECT 'value'; SELECT 2")
        assert result == "SELECT 'value'"


class TestParseSqlType:
    def test_select(self):
        assert parse_sql_type("SELECT * FROM t", "snowflake") == SQLType.SELECT

    def test_select_with_whitespace(self):
        assert parse_sql_type("  SELECT 1  ", "snowflake") == SQLType.SELECT

    def test_insert(self):
        assert parse_sql_type("INSERT INTO t VALUES (1)", "mysql") == SQLType.INSERT

    def test_update(self):
        assert parse_sql_type("UPDATE t SET col=1", "mysql") == SQLType.UPDATE

    def test_delete(self):
        assert parse_sql_type("DELETE FROM t WHERE id=1", "mysql") == SQLType.DELETE

    def test_merge(self):
        assert (
            parse_sql_type(
                "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN UPDATE SET t.v=s.v",
                "snowflake",
            )
            == SQLType.MERGE
        )

    def test_create_table(self):
        assert parse_sql_type("CREATE TABLE t (id INT)", "mysql") == SQLType.DDL

    def test_alter_table(self):
        assert parse_sql_type("ALTER TABLE t ADD COLUMN name VARCHAR(50)", "mysql") == SQLType.DDL

    def test_drop_table(self):
        assert parse_sql_type("DROP TABLE t", "mysql") == SQLType.DDL

    def test_truncate(self):
        assert parse_sql_type("TRUNCATE TABLE t", "mysql") == SQLType.DDL

    def test_show_tables(self):
        assert parse_sql_type("SHOW TABLES", "mysql") == SQLType.METADATA_SHOW

    def test_describe(self):
        assert parse_sql_type("DESCRIBE t", "mysql") == SQLType.METADATA_SHOW

    def test_explain(self):
        assert parse_sql_type("EXPLAIN SELECT 1", "snowflake") == SQLType.EXPLAIN

    def test_use_database(self):
        assert parse_sql_type("USE my_db", "mysql") == SQLType.CONTENT_SET

    def test_set(self):
        assert parse_sql_type("SET search_path TO my_schema", "postgres") == SQLType.CONTENT_SET
        assert parse_sql_type("SET catalog my_cat", "starrocks") == SQLType.CONTENT_SET

    def test_switch_catalog_doris(self):
        assert parse_sql_type("SWITCH internal", "doris") == SQLType.CONTENT_SET

    def test_with_cte_select(self):
        assert parse_sql_type("WITH cte AS (SELECT 1) SELECT * FROM cte", "snowflake") == SQLType.SELECT

    def test_empty_string(self):
        assert parse_sql_type("", "mysql") == SQLType.UNKNOWN

    def test_none_input(self):
        assert parse_sql_type(None, "mysql") == SQLType.UNKNOWN

    def test_whitespace_only(self):
        assert parse_sql_type("   ", "mysql") == SQLType.UNKNOWN

    def test_grant(self):
        assert parse_sql_type("GRANT SELECT ON t TO user1", "mysql") == SQLType.DDL

    def test_begin_transaction(self):
        assert parse_sql_type("BEGIN", "mysql") == SQLType.CONTENT_SET

    def test_commit(self):
        assert parse_sql_type("COMMIT", "mysql") == SQLType.CONTENT_SET

    def test_rollback(self):
        assert parse_sql_type("ROLLBACK", "mysql") == SQLType.CONTENT_SET

    def test_values(self):
        assert parse_sql_type("VALUES (1, 2, 3)", "snowflake") == SQLType.SELECT

    def test_replace(self):
        assert parse_sql_type("REPLACE INTO t VALUES (1)", "mysql") == SQLType.INSERT

    def test_vacuum(self):
        assert parse_sql_type("VACUUM", "snowflake") == SQLType.DDL

    def test_vacuum_postgres(self):
        assert parse_sql_type("VACUUM", "postgres") == SQLType.DDL

    def test_optimize(self):
        assert parse_sql_type("OPTIMIZE TABLE t", "mysql") == SQLType.DDL

    def test_copy(self):
        assert parse_sql_type("COPY INTO t FROM @stage", "snowflake") == SQLType.DDL

    def test_refresh(self):
        assert parse_sql_type("REFRESH MATERIALIZED VIEW mv", "postgres") == SQLType.DDL

    def test_analyze(self):
        assert parse_sql_type("ANALYZE t", "postgres") == SQLType.DDL

    def test_pragma(self):
        assert parse_sql_type("PRAGMA table_info(t)", "sqlite") == SQLType.METADATA_SHOW


class TestParseContextSwitch:
    def test_use_database_mysql(self):
        result = parse_context_switch("USE my_db", "mysql")
        assert result is not None
        assert result["command"] == "USE"
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_use_schema_snowflake(self):
        result = parse_context_switch("USE db.schema1", "snowflake")
        assert result is not None
        assert result["database_name"] == "db"
        assert result["schema_name"] == "schema1"
        assert result["target"] == "schema"

    def test_use_database_snowflake_single(self):
        result = parse_context_switch("USE my_db", "snowflake")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_use_catalog_keyword(self):
        result = parse_context_switch("USE CATALOG my_catalog", "trino")
        assert result is not None
        assert result["catalog_name"] == "my_catalog"
        assert result["target"] == "catalog"

    def test_use_database_keyword(self):
        result = parse_context_switch("USE DATABASE my_db", "snowflake")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_use_schema_keyword(self):
        result = parse_context_switch("USE SCHEMA my_schema", "snowflake")
        assert result is not None
        assert result["schema_name"] == "my_schema"
        assert result["target"] == "schema"

    def test_set_catalog(self):
        result = parse_context_switch("SET CATALOG my_catalog", "trino")
        assert result is not None
        assert result["catalog_name"] == "my_catalog"
        assert result["target"] == "catalog"

    def test_set_database(self):
        result = parse_context_switch("SET DATABASE my_db", "snowflake")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_set_schema(self):
        result = parse_context_switch("SET SCHEMA my_schema", "postgres")
        assert result is not None
        assert result["schema_name"] == "my_schema"
        assert result["target"] == "schema"

    def test_set_schema_with_equals(self):
        result = parse_context_switch("SET SCHEMA = my_schema", "postgres")
        assert result is not None
        assert result["schema_name"] == "my_schema"

    def test_set_schema_with_to(self):
        result = parse_context_switch("SET SCHEMA TO my_schema", "postgres")
        assert result is not None
        assert result["schema_name"] == "my_schema"

    def test_non_context_command_returns_none(self):
        assert parse_context_switch("SELECT 1", "mysql") is None

    def test_empty_input(self):
        assert parse_context_switch("", "mysql") is None

    def test_none_input(self):
        assert parse_context_switch(None, "mysql") is None

    def test_use_duckdb_single_identifier(self):
        result = parse_context_switch("USE my_schema", "duckdb")
        assert result is not None
        assert result["schema_name"] == "my_schema"
        assert result["fuzzy"] is True

    def test_use_duckdb_two_part(self):
        result = parse_context_switch("USE my_db.my_schema", "duckdb")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["schema_name"] == "my_schema"
        assert result["fuzzy"] is False

    def test_use_starrocks(self):
        result = parse_context_switch("USE my_db", "starrocks")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_use_doris_database(self):
        result = parse_context_switch("USE my_db", "doris")
        assert result is not None
        assert result["database_name"] == "my_db"
        assert result["target"] == "database"

    def test_use_doris_catalog_database(self):
        result = parse_context_switch("USE external_catalog.analytics", "doris")
        assert result is not None
        assert result["catalog_name"] == "external_catalog"
        assert result["database_name"] == "analytics"
        assert result["target"] == "database"

    def test_switch_doris_catalog(self):
        result = parse_context_switch("SWITCH `external-catalog`;", "doris")
        assert result is not None
        assert result["command"] == "SWITCH"
        assert result["catalog_name"] == "external-catalog"
        assert result["target"] == "catalog"

    def test_raw_preserved(self):
        result = parse_context_switch("USE my_db", "mysql")
        assert result["raw"] == "USE my_db"

    def test_set_session_schema(self):
        result = parse_context_switch("SET SESSION SCHEMA my_schema", "postgres")
        assert result is not None
        assert result["schema_name"] == "my_schema"


class TestMetadataIdentifier:
    def test_sqlite_with_database(self):
        result = metadata_identifier(database_name="main", table_name="users", dialect="sqlite")
        assert result == "main.users"

    def test_sqlite_without_database(self):
        result = metadata_identifier(table_name="users", dialect="sqlite")
        assert result == "users"

    def test_duckdb(self):
        result = metadata_identifier(database_name="db", schema_name="public", table_name="t", dialect="duckdb")
        assert result == "db.public.t"

    def test_with_registry_capabilities(self):
        from datus_db_core.registry import ConnectorRegistry

        saved_caps = ConnectorRegistry._capabilities.copy()
        try:
            ConnectorRegistry._capabilities["testdialect"] = {"database", "schema"}
            result = metadata_identifier(
                database_name="mydb",
                schema_name="mysch",
                table_name="mytable",
                dialect="testdialect",
            )
            assert result == "mydb.mysch.mytable"
        finally:
            ConnectorRegistry._capabilities = saved_caps

    def test_table_only(self):
        result = metadata_identifier(table_name="users", dialect="snowflake")
        assert result == "users"
