# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_starrocks import StarRocksConfig, StarRocksConnector

METADATA_TABLE = "datus_metadata_table"
METADATA_MV = "datus_metadata_mv"

# ==================== Materialized View Tests (MaterializedViewSupportMixin) ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_materialized_views(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """The fixture materialized view is listed both unscoped and scoped."""
    assert f"{config.database}.{METADATA_MV}" in connector.get_materialized_views(catalog_name=config.catalog)
    assert METADATA_MV in connector.get_materialized_views(
        catalog_name=config.catalog,
        database_name=config.database,
    )


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_materialized_views_with_ddl(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """Every coordinate of a materialized-view entry, plus its stored definition."""
    materialized_view = next(
        item
        for item in connector.get_materialized_views_with_ddl(
            catalog_name=config.catalog,
            database_name=config.database,
        )
        if item["table_name"] == METADATA_MV
    )

    definition = materialized_view["definition"].upper()
    assert "CREATE MATERIALIZED VIEW" in definition
    assert METADATA_TABLE.upper() in definition
    assert "TOTAL_VALUE" in definition

    assert materialized_view["table_type"] == "mv"
    assert materialized_view["catalog_name"] == config.catalog
    assert materialized_view["database_name"] == config.database
    assert materialized_view["schema_name"] == ""
    assert materialized_view["identifier"] == f"{config.catalog}.{config.database}.{METADATA_MV}"


@pytest.mark.integration
def test_materialized_view_identifier_includes_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """A materialized-view identifier is catalog.database.mv — three parts, in that order."""
    materialized_views = connector.get_materialized_views_with_ddl(catalog_name=config.catalog)

    assert METADATA_MV in {item["table_name"] for item in materialized_views}
    for item in materialized_views:
        assert item["identifier"].split(".") == [
            item["catalog_name"],
            item["database_name"],
            item["table_name"],
        ]


@pytest.mark.integration
def test_get_materialized_views_from_specific_catalog(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """A catalog-scoped listing stays inside that catalog after a SET CATALOG."""
    connector.switch_catalog(config.catalog)

    assert METADATA_MV in connector.get_materialized_views(
        catalog_name=config.catalog,
        database_name=config.database,
    )

    materialized_views = connector.get_materialized_views_with_ddl(
        catalog_name=config.catalog,
        database_name=config.database,
    )
    assert METADATA_MV in {item["table_name"] for item in materialized_views}
    for item in materialized_views:
        assert item["catalog_name"] == config.catalog
        assert item["database_name"] == config.database


@pytest.mark.integration
def test_materialized_view_is_not_reported_as_a_view(
    connector: StarRocksConnector,
    config: StarRocksConfig,
    metadata_objects_setup,
):
    """A materialized view is its own object kind, not a view.

    StarRocks reports an asynchronous materialized view as ``TABLE_TYPE='VIEW'``
    in ``information_schema.tables`` and in ``SHOW FULL TABLES``, yet keeps it
    out of ``information_schema.views``. Only the latter answers ``get_views``,
    so the two spellings of "list the views" disagree, and this pins the one the
    adapter is supposed to return.
    """
    scope = {"catalog_name": config.catalog, "database_name": config.database}

    assert METADATA_MV in connector.get_materialized_views(**scope)
    assert METADATA_MV not in connector.get_views(**scope)
    assert METADATA_MV not in connector.get_tables(**scope)
