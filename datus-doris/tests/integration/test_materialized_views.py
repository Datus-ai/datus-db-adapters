# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_doris import DorisConfig, DorisConnector

# ==================== Materialized View Tests (MaterializedViewSupportMixin) ====================

METADATA_MV = "datus_metadata_mv"


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_materialized_views(connector: DorisConnector, config: DorisConfig):
    """Test getting materialized view list."""
    mvs = connector.get_materialized_views(catalog_name=config.catalog, database_name=config.database)
    assert METADATA_MV in mvs


@pytest.mark.integration
@pytest.mark.acceptance
def test_get_materialized_views_with_ddl(connector: DorisConnector):
    """Test getting materialized views with DDL definitions."""
    mvs = connector.get_materialized_views_with_ddl(database_name=connector.database_name)
    mv = next(item for item in mvs if item["table_name"] == METADATA_MV)
    assert "CREATE MATERIALIZED VIEW" in mv["definition"].upper()
    assert mv["table_type"] == "mv"
    assert mv["database_name"] == connector.database_name
    assert mv["schema_name"] == ""
    assert mv["catalog_name"] == connector.default_catalog()


@pytest.mark.integration
def test_materialized_view_identifier_includes_catalog(connector: DorisConnector, config: DorisConfig):
    """Test materialized view identifier includes catalog."""
    mvs = connector.get_materialized_views_with_ddl(
        catalog_name=config.catalog,
        database_name=config.database,
    )
    mv = next(item for item in mvs if item["table_name"] == METADATA_MV)
    assert mv["identifier"] == f"{config.catalog}.{config.database}.{METADATA_MV}"


@pytest.mark.integration
def test_get_materialized_views_from_specific_catalog(connector: DorisConnector, config: DorisConfig):
    """Test getting materialized views from specific catalog."""
    # Switch to the target catalog
    connector.switch_catalog(config.catalog)

    mvs = connector.get_materialized_views(catalog_name=config.catalog, database_name=config.database)
    assert METADATA_MV in mvs

    # Verify all MVs belong to the specified catalog
    mvs_with_ddl = connector.get_materialized_views_with_ddl(catalog_name=config.catalog, database_name=config.database)
    for mv in mvs_with_ddl:
        assert mv["catalog_name"] == config.catalog
        if config.database:
            assert mv["database_name"] == config.database
