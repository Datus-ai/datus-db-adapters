# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_db_core import DatusDbException
from datus_tidb import TiDBConnector

from .conftest import METADATA_TABLE, METADATA_VIEW


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases_hides_every_tidb_system_database(connector: TiDBConnector, metadata_objects_setup, config):
    """METRICS_SCHEMA is TiDB-only and holds hundreds of monitoring views; the
    inherited MySQL filter does not know about it, so it would surface as a user
    database and pollute the catalog."""
    databases = connector.get_databases()
    lowered = {db.lower() for db in databases}

    assert config.database in databases
    assert "metrics_schema" not in lowered
    assert lowered.isdisjoint({"information_schema", "performance_schema", "mysql", "sys"})


@pytest.mark.integration
def test_get_databases_can_include_system_databases(connector: TiDBConnector):
    lowered = {db.lower() for db in connector.get_databases(include_sys=True)}

    assert "information_schema" in lowered


@pytest.mark.integration
def test_get_schemas_is_empty(connector: TiDBConnector):
    """TiDB, like MySQL, has no schema level below the database."""
    assert connector.get_schemas() == []


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_lists_user_tables_only(connector: TiDBConnector, metadata_objects_setup, config):
    tables = connector.get_tables(database_name=config.database)

    assert METADATA_TABLE in tables
    assert METADATA_VIEW not in tables, "a view must not be reported as a table"


@pytest.mark.integration
def test_get_views(connector: TiDBConnector, metadata_objects_setup, config):
    views = connector.get_views(database_name=config.database)

    assert METADATA_VIEW in views


@pytest.mark.integration
def test_unscoped_metadata_excludes_system_objects(connector: TiDBConnector, metadata_objects_setup, config):
    """Without a database filter the listing must still skip system objects —
    TiDB's default utf8mb4_bin collation makes the inherited name comparison
    case-sensitive, and its system databases are reported upper-cased."""
    qualified = connector.get_tables()

    assert f"{config.database}.{METADATA_TABLE}" in qualified
    assert not any(
        name.lower().startswith(("information_schema.", "performance_schema.", "metrics_schema.", "mysql.", "sys."))
        for name in qualified
    )


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema_reports_columns_with_comments(connector: TiDBConnector, metadata_objects_setup, config):
    columns = connector.get_schema(database_name=config.database, table_name=METADATA_TABLE)
    by_name = {column["name"]: column for column in columns}

    assert set(by_name) == {"id", "value"}
    assert by_name["id"]["pk"] is True
    assert by_name["id"]["nullable"] is False
    assert by_name["value"]["comment"] == "the value"
    assert by_name["value"]["type"].lower().startswith("int")


@pytest.mark.integration
def test_get_tables_with_ddl(connector: TiDBConnector, metadata_objects_setup, config):
    entries = connector.get_tables_with_ddl(database_name=config.database, tables=[METADATA_TABLE])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["table_name"] == METADATA_TABLE
    assert entry["database_name"] == config.database
    assert "CREATE TABLE" in entry["definition"].upper()
    # TiDB annotates a clustered primary key with its own optimizer comment.
    assert "clustered_index" in entry["definition"] or "PRIMARY KEY" in entry["definition"].upper()


@pytest.mark.integration
def test_get_views_with_ddl(connector: TiDBConnector, metadata_objects_setup, config):
    entries = connector.get_views_with_ddl(database_name=config.database)
    by_name = {entry["table_name"]: entry for entry in entries}

    assert METADATA_VIEW in by_name
    assert "CREATE" in by_name[METADATA_VIEW]["definition"].upper()


@pytest.mark.integration
def test_get_sample_rows(connector: TiDBConnector, metadata_objects_setup, config):
    samples = connector.get_sample_rows(tables=[METADATA_TABLE], database_name=config.database, top_n=5)

    assert len(samples) == 1
    assert samples[0]["table_name"] == METADATA_TABLE
    assert "id" in samples[0]["sample_rows"]


@pytest.mark.integration
@pytest.mark.acceptance
def test_materialized_views_are_reported_as_unsupported(connector: TiDBConnector, config):
    """TiDB has no MATERIALIZED_VIEWS table. Without the guard the caller gets a
    bare `1146 Table doesn't exist`, which reads like a broken connector."""
    with pytest.raises(DatusDbException, match="no materialized views"):
        connector.get_sample_rows(database_name=config.database, table_type="mv")


@pytest.mark.integration
def test_switch_database_context(connector: TiDBConnector, config):
    connector.switch_context(database_name="information_schema")
    assert connector.get_current_context()["database_name"] == "information_schema"

    connector.switch_context(database_name=config.database)
    assert connector.get_current_context()["database_name"] == config.database
