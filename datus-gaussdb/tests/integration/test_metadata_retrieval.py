# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import uuid

import pytest

from datus_gaussdb import GaussDBConfig, GaussDBConnector


@pytest.fixture
def temp_table(connector: GaussDBConnector, config: GaussDBConfig):
    """Create a throwaway table for the duration of one test."""
    table_name = f"meta_{uuid.uuid4().hex[:8]}"
    connector.execute_ddl(
        f"""
        CREATE TABLE {table_name} (
            id INTEGER PRIMARY KEY,
            name VARCHAR(50),
            amount DECIMAL(10, 2),
            created_at TIMESTAMP
        )
        """
    )
    try:
        yield table_name
    finally:
        connector.execute_ddl(f"DROP TABLE IF EXISTS {table_name}")


# ==================== Databases ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_databases_excludes_templates(connector: GaussDBConnector):
    """Template databases are never offered as datasource targets."""
    databases = connector.get_databases()

    assert isinstance(databases, list)
    assert databases
    assert "template0" not in databases
    assert "template1" not in databases


@pytest.mark.integration
def test_get_databases_includes_connected_database(connector: GaussDBConnector, config: GaussDBConfig):
    """The database we are connected to shows up in the listing."""
    assert config.database in connector.get_databases()


# ==================== Schemas ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schemas_excludes_gaussdb_internals(connector: GaussDBConnector):
    """GaussDB's own schemas (dbe_perf, db4ai, ...) are filtered out."""
    schemas = connector.get_schemas()

    assert "public" in schemas
    hidden = {"pg_catalog", "information_schema", "pg_toast", "dbe_perf", "db4ai", "snapshot", "cstore", "blockchain"}
    assert hidden.isdisjoint(schemas)


@pytest.mark.integration
def test_get_schemas_include_sys(connector: GaussDBConnector):
    """include_sys=True exposes at least one internal schema."""
    schemas = connector.get_schemas(include_sys=True)

    assert "information_schema" in schemas


# ==================== Tables ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_lists_created_table(connector: GaussDBConnector, config: GaussDBConfig, temp_table: str):
    """A freshly created table appears in get_tables for its schema."""
    tables = connector.get_tables(schema_name=config.schema_name)

    # get_tables returns qualified identifiers (database.table), matching
    # the PostgreSQL adapter's behavior.
    assert any(t == temp_table or t.endswith(f".{temp_table}") for t in tables)


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_schema_returns_columns(connector: GaussDBConnector, config: GaussDBConfig, temp_table: str):
    """Column metadata carries names, types and the primary key flag."""
    columns = connector.get_schema(schema_name=config.schema_name, table_name=temp_table)

    by_name = {column["name"]: column for column in columns}
    assert set(by_name) == {"id", "name", "amount", "created_at"}
    assert by_name["id"]["pk"] is True
    assert by_name["id"]["nullable"] is False
    assert by_name["name"]["nullable"] is True
    assert "char" in by_name["name"]["type"].lower()


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_tables_with_ddl_includes_created_table(
    connector: GaussDBConnector, config: GaussDBConfig, temp_table: str
):
    """DDL retrieval returns a CREATE TABLE statement for the new table."""
    tables = connector.get_tables_with_ddl(schema_name=config.schema_name, tables=[temp_table])

    matching = [table for table in tables if table["table_name"] == temp_table]
    assert matching, f"{temp_table} missing from {[t['table_name'] for t in tables]}"
    table = matching[0]
    assert table["table_type"] == "table"
    assert table["schema_name"] == config.schema_name
    assert "CREATE TABLE" in table["definition"]
    assert "amount" in table["definition"]


@pytest.mark.integration
def test_get_views_is_a_list(connector: GaussDBConnector, config: GaussDBConfig):
    """View listing works even when the schema has no views."""
    assert isinstance(connector.get_views(schema_name=config.schema_name), list)


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_materialized_views_on_server_without_pg_matviews(connector: GaussDBConnector, config: GaussDBConfig):
    """openGauss has no pg_matviews view, so the probe reports False and listing returns []."""
    version = connector.execute_query("SELECT version() AS v", result_format="list").sql_return[0]["v"]
    traits = connector._get_traits()

    if "openGauss" in version:
        assert traits.has_matviews is False
        assert connector.get_materialized_views(schema_name=config.schema_name) == []
    else:
        assert isinstance(connector.get_materialized_views(schema_name=config.schema_name), list)


@pytest.mark.integration
def test_get_sample_rows(connector: GaussDBConnector, config: GaussDBConfig, temp_table: str):
    """Sample rows are returned as CSV for a populated table."""
    connector.execute_insert(f"INSERT INTO {temp_table} (id, name) VALUES (1, 'alpha')")

    samples = connector.get_sample_rows(tables=[temp_table], schema_name=config.schema_name)

    assert len(samples) == 1
    assert samples[0]["table_name"] == temp_table
    assert "alpha" in samples[0]["sample_rows"]


# ==================== Feature Probing ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_traits_report_compat_mode(compat_mode: str):
    """The compatibility mode is probed from pg_database, not from version()."""
    assert compat_mode in {"A", "B", "PG"}


@pytest.mark.integration
@pytest.mark.acceptance
def test_traits_report_topology(connector: GaussDBConnector):
    """Single-node openGauss is centralized; pgxc_class only exists when distributed."""
    traits = connector._get_traits()
    version = connector.execute_query("SELECT version() AS v", result_format="list").sql_return[0]["v"]

    if "openGauss" in version:
        assert traits.is_distributed is False
    else:
        assert isinstance(traits.is_distributed, bool)


@pytest.mark.integration
def test_traits_are_cached(connector: GaussDBConnector, config: GaussDBConfig):
    """Repeated probes are served from the per-database cache."""
    first = connector._get_traits()
    second = connector._get_traits()

    assert second is first
    assert set(connector._traits_cache) == {config.database}


@pytest.mark.integration
def test_describe_migration_capabilities(connector: GaussDBConnector):
    """Migration metadata is GaussDB-flavored and warns about A-mode empty strings."""
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["dialect_family"] == "gaussdb"
    assert any("empty strings are stored as NULL" in note for note in capabilities["notes"])
