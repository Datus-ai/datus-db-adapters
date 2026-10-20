# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import pytest

from datus_trino import TrinoConfig, TrinoConnector

# ==================== Connection Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: TrinoConfig):
    """Test connection using TrinoConfig object."""
    conn = TrinoConnector(config)
    assert conn.test_connection()
    conn.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_dict(config: TrinoConfig):
    """Dictionary construction preserves the selected endpoint and TLS policy."""
    conn = TrinoConnector(config.model_dump())
    try:
        assert conn.test_connection()
    finally:
        conn.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_context_manager(config: TrinoConfig):
    """Test connector as context manager."""
    with TrinoConnector(config) as conn:
        assert conn.test_connection()


@pytest.mark.integration
def test_test_connection_method(connector: TrinoConnector):
    """Test the test_connection method."""
    result = connector.test_connection()
    assert result is True


@pytest.mark.integration
def test_connection_cleanup(config: TrinoConfig):
    """Test connection cleanup."""
    conn = TrinoConnector(config)

    try:
        conn.connect()
        assert conn.test_connection()
    finally:
        conn.close()

    # Should not raise exception on re-close
    conn.close()
