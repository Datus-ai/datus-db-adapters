# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_catalog_discovery(connector: DorisConnector):
    catalogs = connector.get_catalogs()
    assert connector.default_catalog() == "internal"
    assert "internal" in catalogs


@pytest.mark.integration
def test_default_catalog_databases(
    connector: DorisConnector,
    config: DorisConfig,
):
    databases = connector.get_databases(include_sys=False)

    assert config.database in databases
    assert {
        "information_schema",
        "mysql",
        "__internal_schema",
    }.isdisjoint(databases)


@pytest.mark.integration
def test_query_external_catalog_without_changing_context(
    connector: DorisConnector,
    hive_catalog_setup: str,
):
    original_context = connector.get_current_context()

    assert "default" in connector.get_databases(
        catalog_name=hive_catalog_setup,
        include_sys=True,
    )
    assert connector.get_current_context() == original_context


@pytest.mark.integration
@pytest.mark.acceptance
def test_switch_external_catalog_and_restore(
    connector: DorisConnector,
    hive_catalog_setup: str,
):
    original_context = connector.get_current_context()
    try:
        connector.switch_catalog(hive_catalog_setup)
        assert connector.catalog_name == hive_catalog_setup
        assert connector.database_name == ""
        assert "default" in connector.get_databases(include_sys=True)
    finally:
        connector.switch_context(
            catalog_name=original_context["catalog_name"],
            database_name=original_context["database_name"],
        )

    assert connector.get_current_context() == original_context
