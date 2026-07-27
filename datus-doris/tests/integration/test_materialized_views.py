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
