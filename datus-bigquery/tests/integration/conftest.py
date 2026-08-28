# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import json
import os
from typing import Generator

import pytest

from datus_bigquery import BigQueryConfig, BigQueryConnector


@pytest.fixture
def config() -> BigQueryConfig:
    """Create BigQuery configuration from environment or defaults."""
    project = os.getenv("BIGQUERY_PROJECT")
    dataset = os.getenv("BIGQUERY_DATASET")
    if not project or not dataset:
        pytest.skip("BIGQUERY_PROJECT and BIGQUERY_DATASET environment variables are required")

    credentials_info = os.getenv("BIGQUERY_CREDENTIALS_INFO")
    parsed_credentials = json.loads(credentials_info) if credentials_info else None

    return BigQueryConfig(
        project=project,
        dataset=dataset,
        credentials_path=os.getenv("BIGQUERY_CREDENTIALS_PATH"),
        credentials_info=parsed_credentials,
        credentials_base64=os.getenv("BIGQUERY_CREDENTIALS_BASE64"),
        billing_project_id=os.getenv("BIGQUERY_BILLING_PROJECT_ID"),
        location=os.getenv("BIGQUERY_LOCATION") or None,
    )


@pytest.fixture
def connector(config: BigQueryConfig) -> Generator[BigQueryConnector, None, None]:
    """Create and cleanup BigQuery connector for integration tests."""
    conn = BigQueryConnector(config)
    try:
        assert conn.test_connection(), "BigQuery connection test returned false"
        yield conn
    finally:
        conn.close()
