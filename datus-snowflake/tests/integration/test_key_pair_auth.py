# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live key pair authentication smoke test.

Requires SNOWFLAKE_PRIVATE_KEY_FILE pointing to a PEM key whose public half is
registered on the Snowflake user (`ALTER USER ... SET RSA_PUBLIC_KEY = '...'`).
"""

import os

import pytest

from datus_snowflake import SnowflakeConfig, SnowflakeConnector

from .conftest import requires_key_pair_credentials

pytestmark = [pytest.mark.integration, requires_key_pair_credentials]


def test_connection_with_key_pair():
    cfg = SnowflakeConfig(
        account=os.getenv("SNOWFLAKE_ACCOUNT", ""),
        username=os.getenv("SNOWFLAKE_USER", ""),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", ""),
        private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE", ""),
        private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE_PWD"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        role=os.getenv("SNOWFLAKE_ROLE"),
    )
    conn = SnowflakeConnector(cfg)
    try:
        assert conn.test_connection()["success"] is True
        result = conn.execute_query('SELECT 1 AS "num"', result_format="list")
        assert result.success
        assert result.sql_return == [{"num": 1}]
    finally:
        conn.close()
