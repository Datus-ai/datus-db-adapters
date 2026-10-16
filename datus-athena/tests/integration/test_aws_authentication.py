import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from datus_athena import AthenaConnector

pytestmark = pytest.mark.integration
logger = logging.getLogger(__name__)


def test_protected_data_requires_service_permissions(role_config, restricted_config, protected_query):
    kind, sql = protected_query
    with AthenaConnector(role_config) as allowed, AthenaConnector(restricted_config) as denied:
        for connector in (allowed, denied):
            baseline = connector.execute_query(
                "SELECT r_regionkey FROM tpch_region ORDER BY r_regionkey LIMIT 1", "list"
            )
            assert baseline.success, baseline.error
            assert baseline.sql_return == [{"r_regionkey": 0}]
        permitted = allowed.execute_query(sql, "list")
        assert permitted.success, permitted.error
        assert permitted.sql_return == [{"r_regionkey": 0}]
        rejected = denied.execute_query(sql, "list")
        assert not rejected.success
        assert rejected.query_execution_id
        assert rejected.execution_info["state"] == "FAILED"
        message = rejected.error.lower()
        if kind == "lakeformation":
            # LF can hide inaccessible columns even when DESCRIBE is granted.
            # The successful control above proves the same column exists.
            assert "lake formation" in message or "not authorized" in message
        else:
            assert any(fragment in message for fragment in ("kms", "decrypt", "access denied"))


def test_cached_client_refreshes_after_real_sts_expiry(role_expiry_connector):
    """Keep the Session/client while AWS really expires a 900-second role session."""
    connector = role_expiry_connector
    first = connector.execute_query("SELECT 1 AS value", "list")
    assert first.success, first.error
    assert first.sql_return == [{"value": 1}]
    client = connector._client
    session = connector._identity._session
    credentials = session.get_credentials()
    assert credentials.method == "assume-role"
    original = credentials.get_frozen_credentials()
    original_expiry = credentials._expiry_time
    fingerprint = hashlib.sha256(original.access_key.encode()).hexdigest()
    assert 0 < (original_expiry - datetime.now(UTC)).total_seconds() <= 905
    deadline = original_expiry + timedelta(seconds=10)
    logger.info("Waiting for real STS expiry at %s", deadline.isoformat())
    while (remaining := (deadline - datetime.now(UTC)).total_seconds()) > 0:
        time.sleep(min(30, remaining))
    assert datetime.now(UTC) > original_expiry

    # A static copy is used only to prove AWS rejects the original expired token.
    stale = boto3.client(
        "sts",
        region_name=connector.config.region,
        aws_access_key_id=original.access_key,
        aws_secret_access_key=original.secret_key,
        aws_session_token=original.token,
        config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1}),
    )
    try:
        with pytest.raises(ClientError) as expired:
            stale.get_caller_identity()
        assert expired.value.response["Error"]["Code"] in {"ExpiredToken", "ExpiredTokenException"}
    finally:
        stale.close()

    renewed = connector.execute_query("SELECT 2 AS value", "list")
    assert renewed.success, renewed.error
    assert renewed.sql_return == [{"value": 2}]
    assert connector._client is client
    assert connector._identity._session is session
    refreshed = credentials.get_frozen_credentials()
    assert hashlib.sha256(refreshed.access_key.encode()).hexdigest() != fingerprint
    assert credentials._expiry_time > original_expiry
    assert credentials._expiry_time > datetime.now(UTC)
