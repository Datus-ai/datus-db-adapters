import datetime as dt
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from datus_db_core.aws import AWSCredentialError, AWSIdentity, AWSIdentityConfig


@pytest.fixture
def aws_sdk():
    return pytest.importorskip("boto3", reason="Install datus-db-core[aws] for SDK-dependent tests")


def test_identity_config_defaults_and_validation():
    assert AWSIdentityConfig().model_dump() == {"region": None, "profile": None, "ca_bundle": None}
    for kwargs in ({"profile": ""}, {"aws_secret_access_key": "unsupported"}):
        with pytest.raises(ValidationError):
            AWSIdentityConfig(**kwargs)


def test_sdk_session_and_clients_are_reused_without_extracting_credentials(aws_sdk):
    config = AWSIdentityConfig(region="us-east-1", profile="cross-account-role", ca_bundle="/ca.pem")
    with patch.object(aws_sdk, "Session") as session:
        identity = AWSIdentity(config)
        first = identity.client("rds")
        assert identity.client("rds") is first
        identity.client("secretsmanager")
        session.assert_called_once_with(profile_name="cross-account-role", region_name="us-east-1")
        assert session.return_value.client.call_count == 2
        session.return_value.client.assert_any_call("rds", verify="/ca.pem")
        session.return_value.get_credentials.assert_not_called()


def test_default_identity_leaves_credential_resolution_to_sdk(aws_sdk):
    with patch.object(aws_sdk, "Session") as session:
        AWSIdentity().client("rds")
        session.assert_called_once_with(profile_name=None, region_name=None)


def test_sdk_client_options_and_session_survive_close_and_reopen():
    Config = pytest.importorskip("botocore.config", reason="Install datus-db-core[aws]").Config

    sdk_config = Config(connect_timeout=3, read_timeout=7, retries={"mode": "standard", "total_max_attempts": 2})
    session = MagicMock()
    first, second = MagicMock(), MagicMock()
    session.client.side_effect = [first, second]
    identity = AWSIdentity(AWSIdentityConfig(ca_bundle="/ca.pem"), session=session, client_config=sdk_config)
    assert identity.client("athena") is first
    assert identity.client("athena") is first
    identity.close()
    identity.close()
    first.close.assert_called_once()
    assert identity.client("athena") is second
    assert session.client.call_count == 2
    session.client.assert_called_with("athena", verify="/ca.pem", config=sdk_config)
    session.get_credentials.assert_not_called()
    identity.close()
    second.close.assert_called_once()


def test_client_cleanup_continues_after_failure_without_logging_sensitive_values(caplog):
    session = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.close.side_effect = RuntimeError("sensitive-signed-url")
    session.client.side_effect = [first, second]
    identity = AWSIdentity(session=session)
    identity.client("athena")
    identity.client("secretsmanager")
    identity.close()
    second.close.assert_called_once()
    assert "RuntimeError" in caplog.text
    assert "sensitive-signed-url" not in caplog.text


def test_missing_sdk_has_actionable_error():
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(AWSCredentialError, match=r"\[aws\] extra"):
            AWSIdentity().client("rds")


def test_aws_errors_preserve_code_without_service_message():
    ClientError = pytest.importorskip("botocore.exceptions", reason="Install datus-db-core[aws]").ClientError
    client = MagicMock()
    client.generate_db_auth_token.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "sensitive signed URL"}}, "GenerateToken"
    )
    identity = AWSIdentity()
    with patch.object(identity, "client", return_value=client):
        with pytest.raises(AWSCredentialError) as exc:
            identity.database_token("cluster.example", 3306, "alice")
    assert str(exc.value) == "IAM database token generation failed (AccessDenied)"
    assert exc.value.__suppress_context__


def test_sdk_refreshes_temporary_credentials_used_by_cached_rds_client(aws_sdk):
    """Signing must observe a renewed SDK identity, even with the same client."""
    RefreshableCredentials = pytest.importorskip("botocore.credentials").RefreshableCredentials
    now = [dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)]
    refreshed = []

    def refresh():
        refreshed.append(True)
        return {
            "access_key": "NEW_ACCESS",
            "secret_key": "new_secret",
            "token": "new_session",
            "expiry_time": (now[0] + dt.timedelta(hours=1)).isoformat(),
        }

    credentials = RefreshableCredentials(
        access_key="OLD_ACCESS",
        secret_key="old_secret",
        token="old_session",
        expiry_time=now[0] + dt.timedelta(hours=1),
        refresh_using=refresh,
        method="test",
        time_fetcher=lambda: now[0],
    )
    session = aws_sdk.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-east-1")
    client = session.client("rds")
    client._request_signer._credentials = credentials
    identity = AWSIdentity()
    with patch("boto3.Session", return_value=session):
        identity._clients["rds"] = client
        token1 = identity.database_token("cluster.example", 5432, "alice")
        now[0] += dt.timedelta(hours=2)
        token2 = identity.database_token("cluster.example", 5432, "alice")
    assert parse_qs(urlsplit("https://" + token1).query)["X-Amz-Credential"][0].startswith("OLD_ACCESS/")
    assert parse_qs(urlsplit("https://" + token2).query)["X-Amz-Credential"][0].startswith("NEW_ACCESS/")
    assert refreshed == [True]
