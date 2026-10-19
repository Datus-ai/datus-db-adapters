# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os

import pytest

from datus_hive import HiveConfig, HiveConnector


@pytest.mark.integration
@pytest.mark.acceptance
def test_connection_with_config_object(config: HiveConfig):
    """Test connection using config object."""
    try:
        conn = HiveConnector(config)
        assert conn.test_connection()
        conn.close()
    except Exception as exc:
        pytest.skip(f"Database not available: {exc}")


@pytest.mark.integration
def test_connection_with_dict():
    """Test connection using dict config."""
    try:
        conn = HiveConnector(
            {
                "host": os.getenv("HIVE_HOST", "localhost"),
                "port": int(os.getenv("HIVE_PORT", "10000")),
                "username": os.getenv("HIVE_USERNAME", "hive"),
                "password": os.getenv("HIVE_PASSWORD", ""),
                "database": os.getenv("HIVE_DATABASE", "default"),
                "auth": os.getenv("HIVE_AUTH") or None,
                "kerberos_service_name": os.getenv("HIVE_KERBEROS_SERVICE_NAME") or None,
            }
        )
        assert conn.test_connection()
        conn.close()
    except Exception as exc:
        pytest.skip(f"Database not available: {exc}")
