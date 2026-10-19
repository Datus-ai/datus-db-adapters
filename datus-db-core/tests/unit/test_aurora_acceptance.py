from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from datus_db_core.testing import aurora


def test_unconfigured_cloud_environment_skips_before_instantiating_connector(monkeypatch):
    monkeypatch.delenv("DATUS_AURORA_UNIT_CONFIG", raising=False)
    connector_class = MagicMock()
    with pytest.raises(pytest.skip.Exception, match="not set"):
        with aurora.cloud_connector(connector_class, MagicMock(), "DATUS_AURORA_UNIT"):
            raise AssertionError("Missing environment must not yield a connector")
    connector_class.assert_not_called()


def test_configured_cloud_connection_errors_are_failures_and_connector_is_closed(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_UNIT_CONFIG", '{"aurora":true}')
    connector = MagicMock()
    connector.execute_query.return_value = SimpleNamespace(success=False, error="authentication failed")
    with pytest.raises(AssertionError, match="authentication failed"):
        with aurora.cloud_connector(lambda config: connector, SimpleNamespace, "DATUS_AURORA_UNIT") as pair:
            aurora.assert_query_and_metadata(*pair)
    connector.close.assert_called_once()


def test_mutation_cases_require_explicit_opt_in_before_contacting_aws(monkeypatch):
    monkeypatch.delenv("DATUS_AURORA_MUTATION_TESTS", raising=False)
    with patch.object(aurora, "AWSIdentity") as identity:
        with pytest.raises(pytest.skip.Exception, match="MUTATION_TESTS"):
            aurora.assert_secret_rotation(MagicMock(), MagicMock())
        identity.assert_not_called()


def test_secret_rotation_waits_for_current_version_before_refreshing_pools(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_MUTATION_TESTS", "1")
    monkeypatch.delenv("DATUS_AURORA_ROTATION_CLUSTER_ID", raising=False)
    client = MagicMock()
    client.describe_secret.side_effect = [
        {"VersionIdsToStages": {"old": ["AWSCURRENT"]}},
        {"VersionIdsToStages": {"old": ["AWSCURRENT"], "new": ["AWSPENDING"]}},
        {"VersionIdsToStages": {"new": ["AWSCURRENT"]}},
    ]
    client.rotate_secret.return_value = {"VersionId": "new"}
    config = SimpleNamespace(secret_ref="disposable-secret", aws=None)
    connector = MagicMock()
    connector.execute_query.return_value = SimpleNamespace(success=True)
    with patch.object(aurora, "AWSIdentity") as identity, patch.object(aurora.time, "sleep"):
        identity.return_value.client.return_value = client
        aurora.assert_secret_rotation(connector, config)
    client.rotate_secret.assert_called_once_with(SecretId="disposable-secret")
    assert client.describe_secret.call_count == 3
    connector.refresh_credentials.assert_called_once()


def test_managed_master_rotation_checks_ownership_and_waits_for_new_current_version(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_MUTATION_TESTS", "1")
    monkeypatch.setenv("DATUS_AURORA_ROTATION_CLUSTER_ID", "disposable-cluster")
    secrets = MagicMock()
    secrets.describe_secret.side_effect = [
        {"ARN": "managed-secret", "VersionIdsToStages": {"old": ["AWSCURRENT"]}},
        {"VersionIdsToStages": {"old": ["AWSCURRENT"], "new": ["AWSPENDING"]}},
        {"VersionIdsToStages": {"old": ["AWSPREVIOUS"], "new": ["AWSCURRENT"]}},
    ]
    rds = MagicMock()
    rds.describe_db_clusters.return_value = {"DBClusters": [{"MasterUserSecret": {"SecretArn": "managed-secret"}}]}
    connector = MagicMock()
    connector.execute_query.return_value = SimpleNamespace(success=True)
    config = SimpleNamespace(secret_ref="managed-secret", aws=None)
    with patch.object(aurora, "AWSIdentity") as identity, patch.object(aurora.time, "sleep"):
        identity.return_value.client.side_effect = lambda name: {"rds": rds, "secretsmanager": secrets}[name]
        aurora.assert_secret_rotation(connector, config)
    rds.modify_db_cluster.assert_called_once_with(
        DBClusterIdentifier="disposable-cluster", RotateMasterUserPassword=True, ApplyImmediately=True
    )
    secrets.rotate_secret.assert_not_called()
    assert secrets.describe_secret.call_count == 3
    connector.refresh_credentials.assert_called_once()


def test_managed_rotation_rejects_secret_from_another_cluster_before_mutation(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_MUTATION_TESTS", "1")
    monkeypatch.setenv("DATUS_AURORA_ROTATION_CLUSTER_ID", "disposable-cluster")
    secrets = MagicMock()
    secrets.describe_secret.return_value = {"ARN": "other-secret", "VersionIdsToStages": {"old": ["AWSCURRENT"]}}
    rds = MagicMock()
    rds.describe_db_clusters.return_value = {"DBClusters": [{"MasterUserSecret": {"SecretArn": "managed-secret"}}]}
    connector = MagicMock()
    connector.execute_query.return_value = SimpleNamespace(success=True)
    config = SimpleNamespace(secret_ref="other-secret", aws=None)
    with patch.object(aurora, "AWSIdentity") as identity:
        identity.return_value.client.side_effect = lambda name: {"rds": rds, "secretsmanager": secrets}[name]
        with pytest.raises(AssertionError, match="configured cluster"):
            aurora.assert_secret_rotation(connector, config)
    rds.modify_db_cluster.assert_not_called()
    secrets.rotate_secret.assert_not_called()


def test_token_expiry_keeps_one_session_active_then_rejects_expired_token_and_reconnects(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_LONG_TESTS", "1")
    monkeypatch.delenv("DATUS_AURORA_ROLE_EXPIRY_TEST", raising=False)
    connector = MagicMock()
    connector._url_defaults = {"sslrootcert": "trusted-ca.pem", "connect_timeout": "60"}
    session = SimpleNamespace(success=True, sql_return=[{"session_id": 10}], error=None)
    rejected = SimpleNamespace(success=False, error="authentication failed")
    fresh = SimpleNamespace(success=True, sql_return=[{"session_id": 20}], error=None)
    connector.execute_query.side_effect = [session] * 18 + [rejected, fresh]
    connector._connection_parameters.return_value = {"user": "test", "password": "synthetic-token"}
    config = SimpleNamespace(auth_mode="iam")
    with patch.object(aurora.time, "sleep") as sleep:
        aurora.assert_token_lifetime(connector, config, "SELECT session_id")
    assert sleep.call_count == 16
    assert connector.execute_query.call_count == 20
    assert connector.configure_connection.call_count == 2
    for call in connector.configure_connection.call_args_list:
        assert call.kwargs["url_defaults"] == {"sslrootcert": "trusted-ca.pem", "connect_timeout": "60"}
    replacement = connector.configure_connection.call_args.kwargs["connection_parameters"]
    assert replacement is connector._connection_parameters


def test_token_lifetime_fails_if_heartbeat_moves_to_another_physical_session(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_LONG_TESTS", "1")
    monkeypatch.delenv("DATUS_AURORA_ROLE_EXPIRY_TEST", raising=False)
    connector = MagicMock()
    connector.execute_query.side_effect = [
        SimpleNamespace(success=True, sql_return=[{"session_id": 10}], error=None),
        SimpleNamespace(success=True, sql_return=[{"session_id": 20}], error=None),
    ]
    with patch.object(aurora.time, "sleep"), pytest.raises(AssertionError):
        aurora.assert_token_lifetime(connector, SimpleNamespace(auth_mode="iam"), "SELECT session_id")
    connector.configure_connection.assert_not_called()


def test_failover_waits_for_new_writer_and_uses_only_read_only_probe(monkeypatch):
    monkeypatch.setenv("DATUS_AURORA_MUTATION_TESTS", "1")
    monkeypatch.setenv("DATUS_AURORA_UNIT_CLUSTER_ID", "disposable-cluster")
    client = MagicMock()

    def describe(writer):
        return {
            "DBClusters": [
                {
                    "Endpoint": "writer.example",
                    "Status": "available",
                    "DBClusterMembers": [{"DBInstanceIdentifier": writer, "IsClusterWriter": True}],
                }
            ]
        }

    client.describe_db_clusters.side_effect = [describe("old"), describe("old"), describe("new")]

    class RecoveringConnector:
        def __init__(self):
            self.pool = object()
            self.seen_pools = []
            self.probes = []
            self.pending = [
                SimpleNamespace(success=True, sql_return=[{"is_writer": True}]),
                SimpleNamespace(success=False, error="connection lost"),
                SimpleNamespace(success=True, sql_return=[{"is_writer": False}]),
                SimpleNamespace(success=True, sql_return=[{"is_writer": True}]),
            ]

        def execute_query(self, sql, result_format):
            self.seen_pools.append(self.pool)
            self.probes.append((sql, result_format))
            return self.pending.pop(0)

        def close(self):
            self.pool = object()
            self.pending = [SimpleNamespace(success=True, sql_return=[{"is_writer": True}])]

    connector = RecoveringConnector()
    original_pool = connector.pool
    config = SimpleNamespace(host="writer.example", aws=None)
    sql = "SELECT NOT pg_is_in_recovery() AS is_writer"
    with patch.object(aurora, "AWSIdentity") as identity, patch.object(aurora.time, "sleep"):
        identity.return_value.client.return_value = client
        aurora.assert_failover(connector, config, "DATUS_AURORA_UNIT", sql)
    client.failover_db_cluster.assert_called_once_with(DBClusterIdentifier="disposable-cluster")
    assert client.describe_db_clusters.call_count == 3
    assert connector.probes == [(sql, "list")] * 4
    assert connector.seen_pools == [original_pool] * 4
