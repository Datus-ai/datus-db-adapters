# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import os
from typing import Generator

import pytest
from datus_bigquery import BigQueryConfig, BigQueryConnector


@pytest.fixture
def config() -> BigQueryConfig:
    """Create BigQuery configuration from environment or defaults."""
    project = os.getenv("BIGQUERY_PROJECT")
    if not project:
        pytest.skip("BIGQUERY_PROJECT environment variable not set")

    return BigQueryConfig(
        project=project,
        dataset=os.getenv("BIGQUERY_DATASET", "datus_test"),
        credentials_path=os.getenv("BIGQUERY_CREDENTIALS_PATH"),
        location=os.getenv("BIGQUERY_LOCATION", "US"),
    )


@pytest.fixture
def connector(config: BigQueryConfig) -> Generator[BigQueryConnector, None, None]:
    """Create and cleanup BigQuery connector for integration tests."""
    conn = None
    try:
        conn = BigQueryConnector(config)
        if not conn.test_connection():
            pytest.skip("BigQuery connection test failed")
    except Exception as e:
        pytest.skip(f"BigQuery not available: {e}")
    else:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
