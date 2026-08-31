# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Metadata discovery and DDL fidelity against a live DWS cluster."""

import pytest

from datus_dws import DWSConnector


def _ddl_for(connector: DWSConnector, table: str) -> str:
    return connector._get_ddl(connector.schema_name, table)


@pytest.mark.integration
@pytest.mark.acceptance
def test_object_discovery(dws_objects: DWSConnector):
    connector = dws_objects
    schema = connector.schema_name

    assert connector.database_name in connector.get_databases()
    assert schema in connector.get_schemas()

    tables = set(connector.get_tables(schema_name=schema))
    for expected in ("t_row_hash", "t_col_compress", "t_replication", "t_roundrobin", "t_partitioned"):
        assert any(expected in name for name in tables), f"{expected} not discovered in {tables}"

    views = connector.get_views(schema_name=schema)
    assert any("v_rows" in name for name in views)


@pytest.mark.integration
def test_dws_system_schemas_are_hidden(dws_objects: DWSConnector):
    schemas = dws_objects.get_schemas()

    # These exist on every DWS cluster; leaking them buries the user's own
    # schemas and is exactly what the base class's filter misses.
    for system_schema in ("cstore", "sys", "pg_recyclebin", "gs_logical_cluster", "dbms_output"):
        assert system_schema not in schemas

    assert dws_objects.get_schemas(include_sys=True) != schemas


@pytest.mark.integration
def test_materialized_views_are_gated_on_the_server_switch(dws_objects: DWSConnector):
    traits = dws_objects._get_traits()
    result = dws_objects.get_materialized_views(schema_name=dws_objects.schema_name)

    if not traits.enable_matview:
        assert result == []
    else:
        assert isinstance(result, list)


@pytest.mark.integration
@pytest.mark.acceptance
def test_column_metadata(dws_objects: DWSConnector):
    columns = dws_objects.get_schema(schema_name=dws_objects.schema_name, table_name="t_row_hash")

    by_name = {column["name"]: column for column in columns}
    assert set(by_name) == {"id", "name", "amt"}
    assert by_name["id"]["nullable"] is False
    assert by_name["name"]["nullable"] is True
    assert "anon" in str(by_name["name"]["default_value"])


@pytest.mark.integration
@pytest.mark.acceptance
def test_row_store_ddl_keeps_orientation_and_hash_distribution(dws_objects: DWSConnector):
    ddl = _ddl_for(dws_objects, "t_row_hash")

    assert "orientation=row" in ddl
    assert "DISTRIBUTE BY HASH" in ddl.upper()
    # Rebuilding from column metadata loses precision; pg_get_tabledef does not.
    assert "character varying(64)" in ddl
    assert "numeric(10,2)" in ddl


@pytest.mark.integration
@pytest.mark.acceptance
def test_column_store_ddl_keeps_compression(dws_objects: DWSConnector):
    ddl = _ddl_for(dws_objects, "t_col_compress")

    assert "orientation=column" in ddl
    assert "compression=middle" in ddl


@pytest.mark.integration
@pytest.mark.parametrize(
    "table,marker",
    [
        ("t_replication", "DISTRIBUTE BY REPLICATION"),
        ("t_roundrobin", "DISTRIBUTE BY ROUNDROBIN"),
    ],
)
def test_non_hash_distributions_survive(dws_objects: DWSConnector, table: str, marker: str):
    assert marker in _ddl_for(dws_objects, table).upper()


@pytest.mark.integration
def test_partitioned_table_ddl_keeps_partitions(dws_objects: DWSConnector):
    ddl = _ddl_for(dws_objects, "t_partitioned").upper()

    assert "PARTITION BY RANGE" in ddl
    assert "P2026" in ddl
    assert "MAXVALUE" in ddl


@pytest.mark.integration
def test_ddl_is_not_the_incomplete_fallback(dws_objects: DWSConnector):
    """pg_get_tabledef must actually be used, not silently fallen back from."""
    ddl = _ddl_for(dws_objects, "t_col_compress")

    assert "WARNING" not in ddl
    assert "rebuilt from column metadata" not in ddl


@pytest.mark.integration
def test_cluster_specific_clauses_can_be_stripped_from_real_ddl(dws_objects: DWSConnector):
    ddl = _ddl_for(dws_objects, "t_col_compress")
    stripped = DWSConnector.strip_cluster_specific_clauses(ddl)

    assert "TO GROUP" not in stripped.upper()
    assert "TABLESPACE" not in stripped.upper()
    # Stripping portability blockers must not take the storage semantics with it.
    assert "orientation=column" in stripped
    assert "DISTRIBUTE BY HASH" in stripped.upper()
    assert stripped.rstrip().endswith(";")


@pytest.mark.integration
def test_view_ddl_is_available(dws_objects: DWSConnector):
    ddl = dws_objects._get_ddl(dws_objects.schema_name, "v_rows", object_type="VIEW")

    assert "CREATE VIEW" in ddl.upper()
    assert "T_ROW_HASH" in ddl.upper()
