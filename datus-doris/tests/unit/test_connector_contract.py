# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Contract-level unit coverage: namespace helpers, filters, and failure paths.

These exercise the branches the behavioural tests never reach — identifier
rendering without a catalog, the DDL retargeting fallbacks, and the
degrade-instead-of-raise paths that only fire when the server misbehaves.
"""

from unittest.mock import MagicMock, patch

import pytest

from datus_db_core import connector_registry
from datus_doris import DorisConfig, DorisConnector, register


def _connector(**overrides) -> DorisConnector:
    config = DorisConfig(username="test_user", **overrides)
    with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
        return DorisConnector(config)


@pytest.fixture
def connector() -> DorisConnector:
    conn = _connector()
    conn.catalog_name = "internal"
    conn.database_name = ""
    conn.schema_name = ""
    return conn


# ==================== Namespace helpers ====================


def test_quote_identifier_uses_backticks_and_escapes_them(connector):
    assert connector.quote_identifier("plain") == "`plain`"
    assert connector.quote_identifier("we`ird") == "`we``ird`"


def test_full_name_without_a_table_qualifier(connector):
    """``_resolve_catalog`` always yields a catalog, so the bare form needs a blank one."""
    with patch.object(DorisConnector, "_resolve_catalog", return_value=""):
        assert connector.full_name(table_name="t") == "`t`"
        assert connector.full_name(database_name="db", table_name="t") == "`db`.`t`"


def test_full_name_defaults_to_the_internal_catalog(connector):
    assert connector.full_name(database_name="db", table_name="t") == "`internal`.`db`.`t`"


def test_full_name_drops_the_database_when_only_a_catalog_is_known(connector):
    assert connector.full_name(catalog_name="external", table_name="t") == "`t`"


def test_get_schemas_is_empty_because_doris_has_no_schema_level(connector):
    assert connector.get_schemas() == []
    assert connector.get_schemas(catalog_name="external", database_name="db") == []


def test_effective_capabilities_match_the_registered_set(connector):
    saved = {
        "connectors": connector_registry._connectors.copy(),
        "metadata": connector_registry._metadata.copy(),
        "capabilities": connector_registry._capabilities.copy(),
        "uri_builders": connector_registry._uri_builders.copy(),
        "context_resolvers": connector_registry._context_resolvers.copy(),
    }
    try:
        register()
        assert connector.get_effective_capabilities() == {"catalog", "database"}
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


def test_reset_filter_tables_keeps_an_explicitly_requested_catalog(connector):
    """The MySQL base drops the catalog before delegating; Doris must not."""
    connector.database_name = "current_db"

    assert connector._reset_filter_tables(["t"], catalog_name="external") == ["`external`.`current_db`.`t`"]


def test_reset_filter_tables_falls_back_to_the_current_context(connector):
    connector.catalog_name = "external"
    connector.database_name = "current_db"

    assert connector._reset_filter_tables(["t"]) == ["`external`.`current_db`.`t`"]


def test_reset_filter_tables_is_empty_without_tables(connector):
    assert connector._reset_filter_tables(None) == []
    assert connector._reset_filter_tables([]) == []


# ==================== dry_run_ddl retargeting ====================


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE db.t (id BIGINT)",
        "create table db.t (id BIGINT)",
        "CREATE TABLE IF NOT EXISTS db.t (id BIGINT)",
        "CREATE EXTERNAL TABLE db.t (id BIGINT)",
        "CREATE TEMPORARY TABLE IF NOT EXISTS db.t (id BIGINT)",
        "CREATE TABLE `db`.`t` (id BIGINT)",
    ],
)
def test_retarget_ddl_rewrites_the_create_target(ddl):
    retargeted = DorisConnector._retarget_ddl(ddl, "`internal`.`db`.`scratch`")

    assert "`internal`.`db`.`scratch`" in retargeted
    assert "db.t" not in retargeted.replace("`internal`.`db`.`scratch`", "")


def test_retarget_ddl_rewrites_the_connectors_own_rendering():
    """The CREATE target is rewritten whatever spelling it arrives in."""
    ddl = "CREATE TABLE `internal`.`db`.`t` (id BIGINT) DUPLICATE KEY(id)"

    retargeted = DorisConnector._retarget_ddl(ddl, "`internal`.`db`.`scratch`")

    assert retargeted == "CREATE TABLE `internal`.`db`.`scratch` (id BIGINT) DUPLICATE KEY(id)"


def test_retarget_ddl_ignores_the_qualified_name_outside_the_create_target():
    """The CREATE target wins over an earlier textual occurrence of the same name.

    A leading comment naming the table used to consume the single substring
    replacement, leaving CREATE TABLE pointing at the real table — which
    dry_run_ddl would then create for real and never drop, since cleanup only
    drops the scratch name.
    """
    ddl = "/* rebuild of `internal`.`db`.`t` */\nCREATE TABLE `internal`.`db`.`t` (id BIGINT) DUPLICATE KEY(id)"

    retargeted = DorisConnector._retarget_ddl(ddl, "`internal`.`db`.`scratch`")

    assert "CREATE TABLE `internal`.`db`.`scratch` (" in retargeted
    assert "CREATE TABLE `internal`.`db`.`t` (" not in retargeted


@pytest.mark.parametrize(
    "ddl",
    [
        "ALTER TABLE `internal`.`db`.`t` ADD COLUMN c INT",
        "ALTER TABLE t ADD COLUMN c INT",
        "ALTER TABLE tenant_t ADD COLUMN c INT",
        "-- rebuild t\nDROP TABLE t",
        "/* t backup */ TRUNCATE TABLE t",
        "SELECT 1",
    ],
)
def test_retarget_ddl_leaves_a_non_create_statement_alone(ddl):
    """Only a CREATE TABLE target is rewritten; dry_run_ddl skips the rest.

    A textual fallback used to rewrite the first occurrence of the table name
    anywhere in the statement. Where that occurrence sat in a comment, the
    rewrite landed there and the real target survived — while dry_run_ddl's
    guard saw the scratch name and executed the statement, so a DROP or a
    TRUNCATE reached the real table.
    """
    assert DorisConnector._retarget_ddl(ddl, "`internal`.`db`.`scratch`") == ddl


def test_dry_run_ddl_refuses_to_execute_when_retargeting_fails(connector):
    """An unretargeted statement would run against whatever it already names."""
    connector.database_name = "db"
    connector.execute_ddl = MagicMock()

    errors = connector.dry_run_ddl("SELECT 1", "")

    assert any("skipped the server-side dry run" in error for error in errors)
    connector.execute_ddl.assert_not_called()


def test_dry_run_ddl_refuses_a_destructive_statement_naming_the_table_in_a_comment(connector):
    """The comment used to absorb the rewrite, letting DROP reach the real table.

    The bare-name fallback rewrote `t` inside the leading comment, which put the
    scratch name in the statement and satisfied the guard while `DROP TABLE t`
    still named the real table. Only a CREATE TABLE target is rewritten now, so
    this is refused before execute_ddl is reached.
    """
    connector.database_name = "db"
    connector.execute_ddl = MagicMock()

    errors = connector.dry_run_ddl("-- rebuild t\nDROP TABLE t", "db.t")

    assert any("skipped the server-side dry run" in error for error in errors)
    connector.execute_ddl.assert_not_called()


def test_dry_run_ddl_drops_the_scratch_table_after_a_failed_create(connector):
    connector.database_name = "db"
    connector.execute_ddl = MagicMock(side_effect=[MagicMock(success=False, error="boom"), MagicMock(success=True)])

    errors = connector.dry_run_ddl(
        "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1",
        "db.t",
    )

    assert "boom" in errors
    statements = [call.args[0] for call in connector.execute_ddl.call_args_list]
    assert len(statements) == 2
    assert statements[1].startswith("DROP TABLE IF EXISTS ")
    assert "__datus_dry_run_" in statements[1]


def test_dry_run_ddl_drops_the_scratch_table_after_an_exception(connector):
    connector.database_name = "db"
    connector.execute_ddl = MagicMock(side_effect=[RuntimeError("connection lost"), MagicMock(success=True)])

    errors = connector.dry_run_ddl(
        "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1",
        "db.t",
    )

    assert "connection lost" in errors
    assert connector.execute_ddl.call_args_list[-1].args[0].startswith("DROP TABLE IF EXISTS ")


def test_dry_run_ddl_reports_the_create_error_even_when_cleanup_fails(connector):
    connector.database_name = "db"
    connector.execute_ddl = MagicMock(side_effect=[MagicMock(success=False, error="boom"), RuntimeError("drop failed")])

    errors = connector.dry_run_ddl(
        "CREATE TABLE db.t (id BIGINT NOT NULL) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1",
        "db.t",
    )

    assert "boom" in errors
    assert not any("drop failed" in error for error in errors)


# ==================== Degrade-instead-of-raise paths ====================


def test_get_schema_without_a_table_name_is_empty(connector):
    assert connector.get_schema(database_name="db") == []


def test_get_catalogs_handles_an_empty_result(connector):
    connector._execute_pandas = MagicMock(return_value=MagicMock(empty=True))

    assert connector.get_catalogs() == []


def test_get_databases_handles_an_empty_result(connector):
    connector._execute_pandas = MagicMock(return_value=MagicMock(empty=True))

    assert connector.get_databases() == []


@pytest.mark.parametrize("method", ["get_views", "get_materialized_views"])
def test_listing_degrades_to_empty_when_the_server_errors(connector, method):
    connector._get_metadata = MagicMock(side_effect=RuntimeError("server said no"))

    assert getattr(connector, method)() == []


def test_objects_with_ddl_substitutes_a_placeholder_when_show_create_fails(connector):
    connector._get_metadata = MagicMock(
        return_value=[
            {
                "identifier": "internal.db.t",
                "catalog_name": "internal",
                "database_name": "db",
                "schema_name": "",
                "table_name": "t",
                "table_type": "table",
            }
        ]
    )
    connector._show_create = MagicMock(side_effect=RuntimeError("no privilege"))

    result = connector._get_objects_with_ddl("table", None, "internal", "db")

    assert len(result) == 1
    assert result[0]["definition"] == "-- DDL not available for t"


def test_materialized_views_with_ddl_falls_back_to_the_stored_query(connector):
    connector._get_materialized_view_metadata = MagicMock(
        return_value=[
            {
                "identifier": "internal.db.mv",
                "catalog_name": "internal",
                "database_name": "db",
                "schema_name": "",
                "table_name": "mv",
                "table_type": "mv",
                "query_sql": "SELECT 1",
            }
        ]
    )
    connector._show_create = MagicMock(side_effect=RuntimeError("not supported"))

    result = connector.get_materialized_views_with_ddl(catalog_name="internal", database_name="db")

    assert result[0]["definition"] == "SELECT 1"
    assert "query_sql" not in result[0]


def test_close_swallows_known_pymysql_teardown_errors(connector):
    connector.engine = MagicMock()
    with patch("datus_mysql.MySQLConnector.close", side_effect=RuntimeError("struct.error: required argument")):
        connector.close()

    assert connector.engine is None
    assert connector._owns_engine is False


def test_close_reraises_unexpected_errors(connector):
    connector.engine = MagicMock()
    with patch("datus_mysql.MySQLConnector.close", side_effect=RuntimeError("disk on fire")):
        with pytest.raises(RuntimeError, match="disk on fire"):
            connector.close()


def test_test_connection_closes_even_when_cleanup_raises(connector):
    connector.close = MagicMock(side_effect=RuntimeError("cleanup boom"))
    with patch("datus_mysql.MySQLConnector.test_connection", return_value=True):
        assert connector.test_connection() is True

    connector.close.assert_called_once_with()


def test_parse_context_switch_ignores_unrelated_statements():
    from datus_doris.connector import _parse_doris_context_switch

    assert _parse_doris_context_switch("SELECT 1") is None
    assert _parse_doris_context_switch("SWITCH") is None


def test_get_sample_rows_delegates_the_full_table_type(connector):
    with patch("datus_mysql.MySQLConnector.get_sample_rows", return_value=["delegated"]) as delegated:
        assert connector.get_sample_rows(table_type="full") == ["delegated"]

    assert delegated.call_args.kwargs["table_type"] == "full"


@pytest.mark.parametrize("sql", ["SWITCH `", 'SWITCH "unterminated', "SWITCH ("])
def test_parse_context_switch_returns_none_for_an_unparsable_switch_target(sql):
    """A target the SQL parser rejects is reported as "not a context command"."""
    from datus_doris.connector import _parse_doris_context_switch

    assert _parse_doris_context_switch(sql) is None


def test_conn_rolls_back_when_the_catalog_override_body_raises(connector):
    """The divergence branch owns its connection, so it must roll back itself."""
    connector.catalog_name = "internal"
    conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = conn
    connector.engine = engine
    connector._owns_engine = True

    with pytest.raises(RuntimeError, match="query blew up"):
        with connector._conn(catalog_name="external"):
            raise RuntimeError("query blew up")

    conn.rollback.assert_called_once_with()
    conn.close.assert_called_once_with()


def test_conn_still_closes_when_the_rollback_also_fails(connector):
    connector.catalog_name = "internal"
    conn = MagicMock()
    conn.rollback.side_effect = RuntimeError("rollback failed")
    engine = MagicMock()
    engine.connect.return_value = conn
    connector.engine = engine
    connector._owns_engine = True

    with pytest.raises(RuntimeError, match="query blew up"):
        with connector._conn(catalog_name="external"):
            raise RuntimeError("query blew up")

    conn.close.assert_called_once_with()


def test_metadata_without_a_database_filters_system_databases(connector):
    rows = MagicMock()
    rows.__len__.return_value = 0
    connector.connect = MagicMock()
    connector._execute_pandas = MagicMock(return_value=rows)
    connector._get_materialized_view_metadata = MagicMock(return_value=[])

    connector._get_metadata(table_type="table")

    query = connector._execute_pandas.call_args.args[0]
    assert "TABLE_SCHEMA NOT IN" in query
    for system_database in ("information_schema", "mysql", "__internal_schema"):
        assert system_database in query


def test_materialized_view_metadata_is_empty_outside_the_internal_catalog(connector):
    """``mv_infos()`` only knows Doris-managed asynchronous views."""
    connector._execute_pandas = MagicMock()

    assert connector._get_materialized_view_metadata("external") == []
    connector._execute_pandas.assert_not_called()


def test_qualify_name_includes_a_schema_level_when_a_row_carries_one():
    """Doris rows never carry a schema, but the helper must stay level-agnostic."""
    meta = {"database_name": "db", "schema_name": "sch", "table_name": "t"}

    assert DorisConnector._qualify_name(meta, "", "") == "db.sch.t"
    assert DorisConnector._qualify_name(meta, "db", "") == "sch.t"
    assert DorisConnector._qualify_name(meta, "db", "sch") == "t"


def _one_table_metadata():
    return [
        {
            "identifier": "internal.db.wanted",
            "catalog_name": "internal",
            "database_name": "db",
            "schema_name": "",
            "table_name": "wanted",
            "table_type": "table",
        },
        {
            "identifier": "internal.db.skipped",
            "catalog_name": "internal",
            "database_name": "db",
            "schema_name": "",
            "table_name": "skipped",
            "table_type": "table",
        },
    ]


def test_objects_with_ddl_skips_tables_outside_the_requested_filter(connector):
    connector._get_metadata = MagicMock(return_value=_one_table_metadata())
    connector._show_create = MagicMock(return_value="CREATE TABLE ...")

    result = connector._get_objects_with_ddl("table", ["wanted"], "internal", "db")

    assert [item["table_name"] for item in result] == ["wanted"]


def test_sample_rows_skips_filtered_and_empty_tables(connector):
    connector.database_name = "db"
    connector._get_metadata = MagicMock(return_value=_one_table_metadata())
    empty = MagicMock(empty=True)
    connector._execute_pandas = MagicMock(return_value=empty)

    assert connector.get_sample_rows(tables=["wanted"], database_name="db") == []
    # Only the requested table was queried; the filtered one never reached SQL.
    assert connector._execute_pandas.call_count == 1
    assert "`wanted`" in connector._execute_pandas.call_args.args[0]


def test_close_clears_the_engine_even_when_dispose_fails(connector):
    engine = MagicMock()
    engine.dispose.side_effect = RuntimeError("dispose failed")
    connector.engine = engine

    with patch("datus_mysql.MySQLConnector.close", side_effect=RuntimeError("struct.pack error")):
        connector.close()

    assert connector.engine is None
