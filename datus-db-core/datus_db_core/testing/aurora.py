"""Opt-in real Aurora acceptance cases shared by MySQL and PostgreSQL tests.

These cases never provision AWS resources. Rotation/failover require a separate
mutation opt-in and an existing, disposable test cluster. Missing configuration
skips at setup; configured API, connection, or assertion failures fail the test.
"""

import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from datus_db_core.aws import AWSIdentity


@contextmanager
def cloud_connector(connector_class, config_class, prefix):
    raw = os.getenv(f"{prefix}_CONFIG")
    if not raw:
        pytest.skip(f"{prefix}_CONFIG is not set; real Aurora environment is not configured")
    config = config_class(**json.loads(raw))
    if not config.aurora:
        raise ValueError("Cloud acceptance requires aurora=true and verified TLS")
    connector = connector_class(config)
    try:
        yield connector, config
    finally:
        connector.close()


def assert_query_and_metadata(connector, config):
    result = connector.execute_query("SELECT '数据' AS cjk_value, NULL AS missing", result_format="list")
    assert result.success, result.error
    assert result.sql_return == [{"cjk_value": "数据", "missing": None}]
    assert config.database in connector.get_databases()


def assert_token_lifetime(connector, config, session_id_sql):
    if os.getenv("DATUS_AURORA_LONG_TESTS") != "1":
        pytest.skip("Set DATUS_AURORA_LONG_TESTS=1 for the 16-minute real IAM lifetime case")
    if config.auth_mode != "iam":
        pytest.skip("IAM lifetime case requires auth_mode=iam")
    before = connector.execute_query(session_id_sql, result_format="list")
    assert before.success, before.error
    parameters = connector._connection_parameters
    cleanup = connector._connection_cleanup
    expired_parameters = parameters()
    sdk_credentials = None
    if os.getenv("DATUS_AURORA_ROLE_EXPIRY_TEST") == "1":
        sdk_credentials = parameters.__self__.identity._session.get_credentials()
        assert sdk_credentials.method == "assume-role"
        initial_expiry = sdk_credentials._expiry_time
        assert 0 < (initial_expiry - datetime.now(UTC)).total_seconds() <= 960
    # Keep the session active across token expiry. An entirely idle TCP flow can
    # disappear in an intermediate network device and let Serverless auto-pause,
    # which would test idle-network retention instead of authentication lifetime.
    for _ in range(16):
        time.sleep(60)
        active = connector.execute_query(session_id_sql, result_format="list")
        assert active.success, active.error
        assert active.sql_return == before.sql_return
    existing = connector.execute_query(session_id_sql, result_format="list")
    assert existing.success, existing.error
    assert existing.sql_return == before.sql_return
    if sdk_credentials is not None:
        assert datetime.now(UTC) > initial_expiry, "The original STS role session must really expire"
    connector.configure_connection(
        connect_args=connector._connect_args,
        connection_parameters=lambda: expired_parameters,
        connection_cleanup=cleanup,
    )
    expired = connector.execute_query("SELECT 1", result_format="list")
    assert not expired.success, "An expired IAM token must not establish a new physical connection"
    assert "authentication" in str(expired.error).lower() or "access denied" in str(expired.error).lower()
    connector.configure_connection(
        connect_args=connector._connect_args, connection_parameters=parameters, connection_cleanup=cleanup
    )
    fresh = connector.execute_query(session_id_sql, result_format="list")
    assert fresh.success, fresh.error
    assert fresh.sql_return != before.sql_return
    if sdk_credentials is not None:
        assert sdk_credentials._expiry_time > datetime.now(UTC)
        assert sdk_credentials._expiry_time > initial_expiry


def assert_tls_rejection(connector_class, config, invalid, tmp_path):
    """Use a real Aurora endpoint for trust and hostname rejection, without aliases."""
    values = config.model_dump()
    if invalid == "ca":
        ca = tmp_path / "untrusted.crt"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=DisposableUntrustedCA",
                "-keyout",
                str(tmp_path / "untrusted.key"),
                "-out",
                str(ca),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        values["ssl_ca" if "ssl_ca" in values else "sslrootcert"] = str(ca)
    else:
        values["host"] = socket.getaddrinfo(config.host, config.port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    connector = connector_class(values)
    try:
        result = connector.execute_query("SELECT 1", result_format="list")
        assert not result.success
        assert "certificate" in str(result.error).lower(), result.error
    finally:
        connector.close()


def assert_invalid_iam_token(connector, config):
    if config.auth_mode != "iam":
        pytest.skip("Invalid token case requires auth_mode=iam")
    baseline = connector.execute_query("SELECT 1", result_format="list")
    assert baseline.success, baseline.error
    connector.configure_connection(
        connect_args=connector._connect_args,
        connection_parameters=lambda: {"password": "intentionally-invalid-iam-token"},
    )
    invalid = connector.execute_query("SELECT 1", result_format="list")
    assert not invalid.success
    message = str(invalid.error).lower()
    assert "authentication" in message or "access denied" in message


def assert_iam_permission_denied(connector_class, connector, config, prefix):
    profile = os.getenv(f"{prefix}_DENIED_PROFILE")
    if config.auth_mode != "iam" or not profile:
        pytest.skip(f"IAM permission rejection requires auth_mode=iam and {prefix}_DENIED_PROFILE")
    assert connector.execute_query("SELECT 1", result_format="list").success
    values = config.model_dump()
    values["aws"] = {**(values.get("aws") or {}), "profile": profile}
    denied = connector_class(values)
    try:
        result = denied.execute_query("SELECT 1", result_format="list")
        assert not result.success
        message = str(result.error).lower()
        assert "authentication" in message or "access denied" in message, result.error
    finally:
        denied.close()


def assert_reader_endpoint(connector_class, config, prefix, writer_sql):
    _require_mutations()
    cluster_id = os.getenv(f"{prefix}_CLUSTER_ID")
    if not cluster_id:
        pytest.skip(f"{prefix}_CLUSTER_ID is not set")
    cluster = (
        AWSIdentity(config.aws).client("rds").describe_db_clusters(DBClusterIdentifier=cluster_id)["DBClusters"][0]
    )
    assert any(not member["IsClusterWriter"] for member in cluster["DBClusterMembers"])
    values = config.model_dump()
    values["host"] = cluster["ReaderEndpoint"]
    reader = connector_class(values)
    try:

        def read_only_endpoint_ready():
            reader.close()
            result = reader.execute_query(writer_sql, result_format="list")
            return result.success and result.sql_return == [{"is_writer": False}]

        _wait_until(read_only_endpoint_ready, "reader endpoint DNS/read-only availability")
        assert_query_and_metadata(reader, config)
        write = reader.execute_ddl("CREATE TABLE aurora_read_only_probe (id INT)")
        assert not write.success, "A reader endpoint must reject DDL"
        assert "read" in str(write.error).lower(), write.error
    finally:
        reader.close()


def _require_mutations():
    if os.getenv("DATUS_AURORA_MUTATION_TESTS") != "1":
        pytest.skip("Set DATUS_AURORA_MUTATION_TESTS=1 only for a disposable rotation/failover cluster")


def _wait_until(predicate, description, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(10)
    raise AssertionError(f"Timed out waiting for {description}")


def assert_secret_rotation(connector, config):
    _require_mutations()
    if not config.secret_ref:
        pytest.skip("Secret rotation requires secret_ref and managed-master or Lambda rotation")
    identity = AWSIdentity(config.aws)
    client = identity.client("secretsmanager")
    secret = client.describe_secret(SecretId=config.secret_ref)
    previous = secret["VersionIdsToStages"]
    old_current = {version for version, stages in previous.items() if "AWSCURRENT" in stages}
    assert old_current
    assert connector.execute_query("SELECT 1", result_format="list").success
    managed_cluster = os.getenv("DATUS_AURORA_ROTATION_CLUSTER_ID")
    if managed_cluster:
        rds = identity.client("rds")
        cluster = rds.describe_db_clusters(DBClusterIdentifier=managed_cluster)["DBClusters"][0]
        assert cluster.get("MasterUserSecret", {}).get("SecretArn") == secret["ARN"], (
            "Managed rotation requires the configured cluster's master Secret"
        )
        rds.modify_db_cluster(
            DBClusterIdentifier=managed_cluster,
            RotateMasterUserPassword=True,
            ApplyImmediately=True,
        )
        pending = None
    else:
        pending = client.rotate_secret(SecretId=config.secret_ref)["VersionId"]
        assert pending not in old_current

    def rotated():
        versions = client.describe_secret(SecretId=config.secret_ref)["VersionIdsToStages"]
        if managed_cluster:
            return any(version not in old_current and "AWSCURRENT" in stages for version, stages in versions.items())
        return "AWSCURRENT" in versions.get(pending, [])

    _wait_until(rotated, "Secret rotation to AWSCURRENT")
    connector.refresh_credentials()
    result = connector.execute_query("SELECT 1", result_format="list")
    assert result.success, result.error


def assert_failover(connector, config, prefix, writer_sql):
    _require_mutations()
    cluster = os.getenv(f"{prefix}_CLUSTER_ID")
    if not cluster:
        pytest.skip(f"{prefix}_CLUSTER_ID is not set")
    client = AWSIdentity(config.aws).client("rds")

    def describe():
        return client.describe_db_clusters(DBClusterIdentifier=cluster)["DBClusters"][0]

    def writer(description):
        return next(
            member["DBInstanceIdentifier"] for member in description["DBClusterMembers"] if member["IsClusterWriter"]
        )

    previous = describe()
    assert config.host == previous["Endpoint"], "Failover case requires the cluster writer endpoint"
    previous_writer = writer(previous)
    assert connector.execute_query(writer_sql, result_format="list").success
    client.failover_db_cluster(DBClusterIdentifier=cluster)

    def completed():
        description = describe()
        return description["Status"] == "available" and writer(description) != previous_writer

    _wait_until(completed, "a different Aurora writer to become available")

    def reconnected():
        result = connector.execute_query(writer_sql, result_format="list")
        return result.success and result.sql_return == [{"is_writer": True}]

    # Keep the pre-failover pools: the adapter must invalidate stale connections
    # and reconnect itself. Only this read-only probe is repeated, never writes.
    _wait_until(reconnected, "writer endpoint DNS/reconnection")
