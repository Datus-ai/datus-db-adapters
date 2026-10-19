# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: ClickHouseConfig):
    """Test connection using config object."""
    conn = ClickHouseConnector(config)
    assert conn.test_connection()
    conn.close()


@pytest.mark.integration
def test_connection_with_dict(config: ClickHouseConfig):
    """The dict path retains the same TLS configuration as the object path."""
    conn = ClickHouseConnector(config.model_dump())
    try:
        assert conn.test_connection()
    finally:
        conn.close()
