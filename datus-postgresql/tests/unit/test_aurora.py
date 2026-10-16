import subprocess
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector


@pytest.mark.parametrize(
    "kwargs",
    [
        {"auth_mode": "iam", "password": "secret"},
        {"auth_mode": "iam", "secret_ref": "secret"},
        {"secret_ref": "secret", "password": "secret"},
        {"aurora": True},
        {"auth_mode": "iam"},
        {"aurora": True, "sslrootcert": "ca.pem", "sslmode": "require"},
        {"aurora": True, "sslrootcert": "ca.pem", "sslmode": "prefer"},
        {"sslcert": "client.pem"},
    ],
)
def test_authentication_and_tls_rejections(kwargs):
    with pytest.raises(ValidationError):
        PostgreSQLConfig(username="alice", **kwargs)


def test_aurora_password_and_iam_use_verify_full_without_changing_legacy_defaults():
    for auth_mode in ("password", "iam"):
        config = PostgreSQLConfig(username="alice", aurora=True, auth_mode=auth_mode, sslrootcert="ca.pem")
        assert config.sslmode == "verify-full"
    assert PostgreSQLConfig(username="alice").sslmode == "prefer"


def test_base_and_all_database_engines_get_tls_and_new_tokens():
    connector = PostgreSQLConnector(
        {
            "username": "alice",
            "host": "writer.cluster.example",
            "database": "app",
            "auth_mode": "iam",
            "sslrootcert": "ca.pem",
            "connect_timeout_seconds": 7,
        }
    )
    engines = [connector._ensure_engine(), connector._get_engine("app"), connector._get_engine("other")]
    assert len({id(engine) for engine in engines}) == 3
    observed = []
    with patch("datus_db_core.aws.AWSIdentity.database_token", side_effect=["t1", "t2", "t3", "t4"]) as token:
        for engine in [*engines, engines[-1]]:
            with patch.object(engine.dialect, "connect") as connect:
                engine.pool._creator()
                observed.append(connect.call_args.kwargs)
    assert [params["password"] for params in observed] == ["t1", "t2", "t3", "t4"]
    assert [params["dbname"] for params in observed] == ["app", "app", "other", "other"]
    for params in observed:
        assert params["sslmode"] == "verify-full"
        assert params["sslrootcert"] == "ca.pem"
        assert params["connect_timeout"] == 7
    token.assert_called_with("writer.cluster.example", 5432, "alice")
    assert "t1" not in connector.connection_string
    connector.close()


def test_secret_rotation_discards_base_and_every_database_pool():
    connector = PostgreSQLConnector({"username": "alice", "secret_ref": "db-secret", "database": "app"})
    with patch("datus_db_core.aws.AWSIdentity.client") as client:
        client.return_value.get_secret_value.side_effect = [
            {"VersionId": "v1", "SecretString": '{"password":"old"}'},
            {"VersionId": "v2", "SecretString": '{"password":"new"}'},
        ]
        base = connector._ensure_engine()
        first = connector._get_engine("app")
        second = connector._get_engine("other")
        with (
            patch.object(base, "dispose", wraps=base.dispose) as dispose_base,
            patch.object(first, "dispose", wraps=first.dispose) as dispose_first,
            patch.object(second, "dispose", wraps=second.dispose) as dispose_second,
        ):
            connector.refresh_credentials()
            for dispose in (dispose_base, dispose_first, dispose_second):
                dispose.assert_called_once()
        assert connector.engine is None
        assert list(connector._engines) == []
        new = connector._get_engine("other")
        with patch.object(new.dialect, "connect") as connect:
            new.pool._creator()
            assert connect.call_args.kwargs["password"] == "new"
    connector.close()


def test_separate_identities_never_share_database_pools():
    first = PostgreSQLConnector({"username": "alice", "database": "app"})
    second = PostgreSQLConnector({"username": "bob", "database": "app"})
    assert first._get_engine("app") is not second._get_engine("app")
    first.close()
    second.close()


def test_ordinary_postgresql_does_not_import_aws_sdk():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from datus_postgresql import PostgreSQLConnector; import sys; "
            "c=PostgreSQLConnector({'username':'alice'}); assert 'boto3' not in sys.modules; "
            "assert 'botocore' not in sys.modules; c.close()",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
