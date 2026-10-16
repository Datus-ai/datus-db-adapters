import json
from unittest.mock import Mock, patch

import boto3
import pytest
from botocore.stub import Stubber

from datus_db_core import DatusDbException
from datus_sqlalchemy.auth import (
    DatabaseAuthConfig,
    DatabaseCredentials,
    aurora_writer_validator,
    configure_database_connection,
)


def test_secret_cache_version_refresh_and_expired_refresh_failure():
    config = DatabaseAuthConfig(username="alice", secret_ref="db-secret", secret_cache_ttl_seconds=10)
    credentials = DatabaseCredentials(config)
    session = boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
    client = session.client("secretsmanager")
    params = {"SecretId": "db-secret", "VersionStage": "AWSCURRENT"}
    with Stubber(client) as stub, patch.object(credentials.identity, "client", return_value=client):
        for version, password in (("a" * 32, "old"), ("b" * 32, "new")):
            stub.add_response(
                "get_secret_value",
                {"VersionId": version, "SecretString": json.dumps({"username": "alice", "password": password})},
                params,
            )
        stub.add_client_error("get_secret_value", "AccessDeniedException", "do not expose me", expected_params=params)
        with patch("datus_sqlalchemy.auth.time.monotonic", return_value=0):
            assert credentials.secret_version() == "a" * 32
            assert credentials.connection_parameters() == {"user": "alice", "password": "old"}
        with patch("datus_sqlalchemy.auth.time.monotonic", return_value=11):
            assert credentials.secret_version() == "b" * 32
            assert credentials.connection_parameters() == {"user": "alice", "password": "new"}
        with patch("datus_sqlalchemy.auth.time.monotonic", return_value=22):
            with pytest.raises(DatusDbException) as exc:
                credentials.connection_parameters()
            assert "AccessDeniedException" in str(exc.value)
            assert "do not expose me" not in str(exc.value)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize(
    "secret",
    ["not json", "[]", '{"password": 123}', '{"username": "other", "password": "sensitive"}'],
)
def test_secret_payload_and_identity_are_validated_without_exposing_values(secret):
    credentials = DatabaseCredentials(DatabaseAuthConfig(username="alice", secret_ref="secret"))
    with patch.object(credentials.identity, "client") as client:
        client.return_value.get_secret_value.return_value = {"VersionId": "a" * 32, "SecretString": secret}
        with pytest.raises(DatusDbException, match="Invalid database Secret") as exc:
            credentials.connection_parameters()
    assert "sensitive" not in str(exc.value)


def test_forced_refresh_bypasses_cache_ttl():
    credentials = DatabaseCredentials(DatabaseAuthConfig(username="alice", secret_ref="secret"))
    with patch.object(credentials.identity, "client") as client:
        client.return_value.get_secret_value.side_effect = [
            {"VersionId": "a" * 32, "SecretString": '{"password": "old"}'},
            {"VersionId": "b" * 32, "SecretString": '{"password": "new"}'},
        ]
        assert credentials.secret_version() == "a" * 32
        assert credentials.secret_version(force=True) == "b" * 32
        assert credentials.connection_parameters()["password"] == "new"


def test_identity_configuration_is_snapshotted():
    config = DatabaseAuthConfig(username="alice", secret_ref="secret")
    credentials = DatabaseCredentials(config)
    config.username = "changed"
    assert credentials.config.username == "alice"


def test_inline_password_authentication_does_not_contact_aws():
    credentials = DatabaseCredentials(DatabaseAuthConfig(username="alice", password="inline", aurora=True))
    with patch.object(credentials.identity, "client") as client:
        assert credentials.connection_parameters() == {"user": "alice", "password": "inline"}
        client.assert_not_called()


def test_database_provider_cleanup_reopens_clients_with_the_same_session():
    config = DatabaseAuthConfig(username="alice", secret_ref="secret", connect_timeout_seconds=10)
    session, connector = Mock(), Mock()
    clients = [Mock(), Mock()]
    session.client.side_effect = clients
    configure_database_connection(connector, config, {}, dialect="mysql")
    hooks = connector.configure_connection.call_args.kwargs
    with patch("boto3.Session", return_value=session) as factory:
        for client, version in zip(clients, ("v1", "v2")):
            client.get_secret_value.return_value = {"VersionId": version, "SecretString": '{"password":"test"}'}
            assert hooks["credential_version"](True) == version
            hooks["connection_cleanup"]()
            client.close.assert_called_once_with()
        factory.assert_called_once()
    assert session.client.call_count == 2


@pytest.mark.parametrize(
    "dialect,sql", [("mysql", "SELECT @@innodb_read_only"), ("postgresql", "SELECT pg_is_in_recovery()")]
)
def test_writer_endpoint_rejects_demoted_connection_before_application_transaction(dialect, sql):
    from types import SimpleNamespace

    from sqlalchemy.exc import DisconnectionError

    config = SimpleNamespace(aurora=True, auth_mode="password", host="test.cluster-abc123.us-east-1.rds.amazonaws.com")
    validator = aurora_writer_validator(config, dialect)
    connection = Mock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = (True,)
    with pytest.raises(DisconnectionError, match="writer endpoint resolved to a reader"):
        validator(connection)
    cursor.execute.assert_called_once_with(sql)
    cursor.close.assert_called_once_with()
    connection.rollback.assert_called_once_with()
    cursor.fetchone.return_value = (False,)
    validator(connection)


@pytest.mark.parametrize(
    "host,aurora,enabled",
    [
        ("test.cluster-abc123.us-east-1.rds.amazonaws.com", True, True),
        ("test.cluster-abc123.cn-north-1.rds.amazonaws.com.cn", True, True),
        ("test.cluster-ro-abc123.us-east-1.rds.amazonaws.com", True, False),
        ("test.cluster-custom-abc123.us-east-1.rds.amazonaws.com", True, False),
        ("test.abc123.us-east-1.rds.amazonaws.com", True, False),
        ("test.cluster-abc123.us-east-1.rds.amazonaws.com", False, False),
        ("localhost", True, False),
    ],
)
def test_writer_role_contract_applies_only_to_aws_cluster_writer_endpoints(host, aurora, enabled):
    from types import SimpleNamespace

    config = SimpleNamespace(aurora=aurora, auth_mode="password", host=host)
    assert (aurora_writer_validator(config, "postgresql") is not None) is enabled


def test_other_rds_iam_clusters_do_not_inherit_aurora_writer_role_semantics():
    from types import SimpleNamespace

    config = SimpleNamespace(aurora=False, auth_mode="iam", host="test.cluster-abc123.us-east-1.rds.amazonaws.com")
    assert aurora_writer_validator(config, "mysql") is None
