# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector

METADATA_MV = "datus_metadata_mv"


@pytest.mark.integration
@pytest.mark.acceptance
def test_materialized_view_metadata(
    connector: DorisConnector,
    config: DorisConfig,
    metadata_objects_setup,
):
    assert METADATA_MV in connector.get_materialized_views(
        catalog_name=config.catalog,
        database_name=config.database,
    )

    materialized_views = connector.get_materialized_views_with_ddl(
        catalog_name=config.catalog,
        database_name=config.database,
    )
    materialized_view = next(item for item in materialized_views if item["table_name"] == METADATA_MV)
    assert "CREATE MATERIALIZED VIEW" in materialized_view["definition"].upper()
    assert materialized_view["table_type"] == "mv"
    assert materialized_view["catalog_name"] == config.catalog
    assert materialized_view["database_name"] == config.database
    assert materialized_view["schema_name"] == ""
    assert materialized_view["identifier"] == f"{config.catalog}.{config.database}.{METADATA_MV}"


@pytest.mark.integration
def test_synchronous_materialized_view_is_not_a_separate_object(
    connector: DorisConnector,
    config: DorisConfig,
    sync_materialized_view: tuple[str, str],
):
    """A synchronous materialized view is a rollup index, not a listable object.

    Doris exposes only asynchronous materialized views through ``mv_infos()``,
    and a rollup never reaches ``information_schema.tables``. It must therefore
    stay out of every listing, including the table listing it could plausibly
    leak into.
    """
    table_name, view_name = sync_materialized_view
    scope = {"catalog_name": config.catalog, "database_name": config.database}

    assert table_name in connector.get_tables(**scope)
    assert view_name not in connector.get_tables(**scope)
    assert view_name not in connector.get_views(**scope)
    assert view_name not in connector.get_materialized_views(**scope)


@pytest.mark.integration
def test_synchronous_materialized_view_columns_stay_off_the_base_schema(
    connector: DorisConnector,
    config: DorisConfig,
    sync_materialized_view: tuple[str, str],
):
    """The rollup's own columns must not appear in the base table's schema."""
    table_name, _ = sync_materialized_view

    columns = {
        column["name"]
        for column in connector.get_schema(
            catalog_name=config.catalog,
            database_name=config.database,
            table_name=table_name,
        )
    }

    assert columns == {"id", "k", "v"}
    assert not {"mv_k", "mv_id"} & columns
