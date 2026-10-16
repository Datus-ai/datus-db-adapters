import subprocess
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from datus_mysql import MySQLConfig, MySQLConnector


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auth_mode": "unknown"},
        {"auth_mode": "iam", "password": "secret"},
        {"auth_mode": "iam", "secret_ref": "secret"},
        {"secret_ref": "secret", "password": "secret"},
        {"aurora": True},
        {"auth_mode": "iam"},
        {"aurora": True, "ssl_ca": "ca.pem", "ssl_verify_identity": False},
        {"aurora": True, "ssl_ca": "ca.pem", "ssl_verify_cert": False},
        {"ssl_cert": "client.pem"},
        {"secret_cache_ttl_seconds": 0},
        {"connect_timeout_seconds": 0},
    ],
)
def test_authentication_and_tls_rejections(kwargs):
    with pytest.raises(ValidationError):
        MySQLConfig(username="alice", **kwargs)


def test_aurora_password_and_iam_require_certificate_and_hostname_verification():
    for auth_mode in ("password", "iam"):
        config = MySQLConfig(username="alice", aurora=True, auth_mode=auth_mode, ssl_ca="ca.pem")
        assert config.ssl_verify_cert is True
        assert config.ssl_verify_identity is True
    assert MySQLConfig(username="alice").ssl_ca is None


def test_new_physical_connections_get_current_token_and_tls_driver_arguments():
    connector = MySQLConnector(
        dict(
            username="alice",
            host="writer.cluster.example",
            database="app",
            auth_mode="iam",
            ssl_ca="ca.pem",
            connect_timeout_seconds=7,
            read_timeout_seconds=11,
            write_timeout_seconds=13,
        )
    )
    engine = connector._ensure_engine()
    with (
        patch("datus_db_core.aws.AWSIdentity.database_token", side_effect=["token-1", "token-2"]) as token,
        patch.object(engine.dialect, "connect") as connect,
    ):
        engine.pool._creator()
        engine.pool._creator()
    assert [call.kwargs["password"] for call in connect.call_args_list] == ["token-1", "token-2"]
    token.assert_called_with("writer.cluster.example", 3306, "alice")
    for call in connect.call_args_list:
        assert call.kwargs["ssl_ca"] == "ca.pem"
        assert call.kwargs["ssl_verify_cert"] is True
        assert call.kwargs["ssl_verify_identity"] is True
        assert call.kwargs["auth_plugin_map"] == {"mysql_clear_password": None}
        assert call.kwargs["connect_timeout"] == 7
        assert call.kwargs["read_timeout"] == 11
        assert call.kwargs["write_timeout"] == 13
    assert "token-" not in connector.connection_string
    connector.close()


def test_secret_version_refresh_disposes_old_pool_and_uses_new_password():
    connector = MySQLConnector({"username": "alice", "secret_ref": "db-secret"})
    client_results = [
        {"VersionId": "old", "SecretString": '{"password": "old-password"}'},
        {"VersionId": "new", "SecretString": '{"password": "new-password"}'},
    ]
    with patch("datus_db_core.aws.AWSIdentity.client") as client:
        client.return_value.get_secret_value.side_effect = client_results
        first = connector._ensure_engine()
        with patch.object(first.dialect, "connect") as connect:
            first.pool._creator()
            assert connect.call_args.kwargs["password"] == "old-password"
        with patch.object(first, "dispose", wraps=first.dispose) as dispose:
            connector.refresh_credentials()
            dispose.assert_called_once()
        second = connector._ensure_engine()
        assert second is not first
        with patch.object(second.dialect, "connect") as connect:
            second.pool._creator()
            assert connect.call_args.kwargs["password"] == "new-password"
    connector.close()


def test_ordinary_mysql_does_not_import_aws_sdk():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from datus_mysql import MySQLConnector; import sys; "
            "c=MySQLConnector({'username':'alice'}); assert 'boto3' not in sys.modules; "
            "assert 'botocore' not in sys.modules; c.close()",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
