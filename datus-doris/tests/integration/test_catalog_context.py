# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Catalog-level context handling against a real Doris frontend.

These use a catalog whose backend is deliberately unreachable, so they cover
``SWITCH`` and the per-call catalog override without needing a live external
service. Tests that must actually read an external catalog live in
``test_catalog_operations.py``.
"""

import pytest

from datus_doris import DorisConfig, DorisConnector


@pytest.mark.integration
def test_new_catalogs_appear_in_the_listing(
    connector: DorisConnector,
    dangling_catalog: str,
):
    catalogs = connector.get_catalogs()

    assert "internal" in catalogs
    assert dangling_catalog in catalogs


@pytest.mark.integration
@pytest.mark.acceptance
def test_per_call_catalog_override_does_not_disturb_the_stored_context(
    connector: DorisConnector,
    config: DorisConfig,
    dangling_catalog: str,
):
    """The override path must not carry the stored database into the new catalog.

    ``_conn`` takes a separate branch when the requested catalog differs from
    the stored one and no database is passed: the stored database belongs to the
    old catalog and would fail a ``USE`` after ``SWITCH``. The query below
    succeeds only if that branch drops it.
    """
    connector.switch_context(database_name=config.database)
    original_context = connector.get_current_context()
    assert original_context["database_name"] == config.database

    result = connector.execute(
        {"sql_query": "SELECT 1 AS probe"},
        result_format="list",
        catalog_name=dangling_catalog,
    )

    assert result.success, result.error
    assert result.sql_return == [{"probe": 1}]
    assert connector.get_current_context() == original_context


@pytest.mark.integration
def test_switch_catalog_clears_the_stored_database(
    connector: DorisConnector,
    config: DorisConfig,
    dangling_catalog: str,
):
    """The old catalog's database does not exist under the new catalog."""
    connector.switch_context(database_name=config.database)
    assert connector.database_name == config.database

    try:
        connector.switch_catalog(dangling_catalog)

        assert connector.catalog_name == dangling_catalog
        assert connector.database_name == ""
        assert connector.get_current_context() == {
            "catalog_name": dangling_catalog,
            "database_name": "",
            "schema_name": "",
        }
    finally:
        connector.switch_catalog(config.catalog)
        connector.switch_context(database_name=config.database)


@pytest.mark.integration
def test_switch_statement_updates_the_stored_catalog(
    connector: DorisConnector,
    config: DorisConfig,
    dangling_catalog: str,
):
    """``SWITCH`` routed through execute() must update the connector context."""
    connector.switch_context(database_name=config.database)

    try:
        result = connector.execute({"sql_query": f"SWITCH `{dangling_catalog}`"})

        assert result.success, result.error
        assert connector.catalog_name == dangling_catalog
        assert connector.database_name == ""
    finally:
        connector.execute({"sql_query": f"SWITCH `{config.catalog}`"})
        connector.switch_context(database_name=config.database)


@pytest.mark.integration
def test_use_catalog_qualified_database_sets_both_levels(
    connector: DorisConnector,
    config: DorisConfig,
):
    """``USE catalog.database`` carries both coordinates in one statement."""
    result = connector.execute({"sql_query": f"USE `{config.catalog}`.`{config.database}`"})

    assert result.success, result.error
    assert connector.catalog_name == config.catalog
    assert connector.database_name == config.database


@pytest.mark.integration
def test_full_name_addresses_a_table_through_an_explicit_catalog(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    """A three-part name built by the connector must be directly queryable."""
    full_name = connector.full_name(
        catalog_name=config.catalog,
        database_name=config.database,
        table_name="datus_metadata_table",
    )
    assert full_name == f"`{config.catalog}`.`{config.database}`.`datus_metadata_table`"

    result = connector.execute({"sql_query": f"SELECT COUNT(*) AS n FROM {full_name}"}, result_format="list")

    assert result.success, result.error
    assert result.sql_return[0]["n"] == 2


@pytest.mark.integration
def test_reset_filter_tables_qualifies_with_the_requested_catalog(
    connector: DorisConnector,
    config: DorisConfig,
):
    connector.switch_context(database_name=config.database)

    assert connector._reset_filter_tables(["t"], catalog_name=config.catalog) == [
        f"`{config.catalog}`.`{config.database}`.`t`"
    ]
