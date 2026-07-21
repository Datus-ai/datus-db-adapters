# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os

import pytest

from datus_doris import DorisConfig, DorisConnector

# ==================== Connection Tests ====================


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: DorisConfig):
    """Test connection using DorisConfig object."""
    conn = DorisConnector(config)
    assert conn.test_connection()
    conn.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_dict():
    """Test connection using dict config."""
    conn = DorisConnector(
        {
            "host": os.getenv("DORIS_HOST", "localhost"),
            "port": int(os.getenv("DORIS_PORT", "9030")),
            "username": os.getenv("DORIS_USER", "root"),
            "password": os.getenv("DORIS_PASSWORD", ""),
        }
    )
    assert conn.test_connection()
    conn.close()


@pytest.mark.integration
@pytest.mark.acceptance
def test_context_manager(config: DorisConfig):
    """Test connector as context manager."""
    with DorisConnector(config) as conn:
        assert conn.test_connection()
    # Connection should be closed after context


@pytest.mark.integration
def test_test_connection_method(connector: DorisConnector):
    """Test the test_connection method."""
    result = connector.test_connection()
    assert result is True


@pytest.mark.integration
def test_connection_cleanup_on_error(config: DorisConfig):
    """Test connection cleanup when errors occur."""
    conn = DorisConnector(config)

    try:
        conn.connect()
        # Connection is open
        assert conn.test_connection()
    finally:
        # Cleanup should handle PyMySQL errors gracefully
        conn.close()
        # Should not raise exception

    # Verify connection is closed (no exception on re-close)
    conn.close()
