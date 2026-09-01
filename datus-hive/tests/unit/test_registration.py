# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from datus_db_core.registry import ConnectorRegistry
from datus_hive import register


def test_registers_database_namespace():
    """Hive identifiers use database.table, without catalog or schema."""
    saved_connectors = ConnectorRegistry._connectors.copy()
    saved_metadata = ConnectorRegistry._metadata.copy()
    saved_capabilities = ConnectorRegistry._capabilities.copy()
    try:
        register()

        assert ConnectorRegistry.get_capabilities("hive") == {"database"}
        assert ConnectorRegistry.support_database("hive")
        assert not ConnectorRegistry.support_catalog("hive")
        assert not ConnectorRegistry.support_schema("hive")
    finally:
        ConnectorRegistry._connectors = saved_connectors
        ConnectorRegistry._metadata = saved_metadata
        ConnectorRegistry._capabilities = saved_capabilities
