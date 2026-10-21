# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from datus_db_core import DatusDbException
from datus_db_core.aws import AWSIdentity
from datus_db_core.secrets import AWSSecretProvider


def test_cache_expiry_and_forced_refresh_preserve_atomic_snapshot(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("datus_db_core.secrets.time.monotonic", lambda: now[0])
    client = Mock()
    client.get_secret_value.side_effect = [
        {"VersionId": "v1", "SecretString": '{"password":"first"}'},
        {"VersionId": "v2", "SecretString": '{"password":"second"}'},
        {"VersionId": "v3", "SecretString": '{"password":"third"}'},
    ]
    identity = Mock()
    identity.client.return_value = client
    provider = AWSSecretProvider(identity, "test-secret", 10, json.loads)
    first = provider.load()
    assert (first.version, first.value, first.expires_at) == ("v1", {"password": "first"}, 10)
    now[0] = 9
    assert provider.load() is first
    assert client.get_secret_value.call_count == 1
    now[0] = 10
    assert provider.load().value == {"password": "second"}
    assert provider.load(force=True).version == "v3"
    client.get_secret_value.assert_called_with(SecretId="test-secret", VersionStage="AWSCURRENT")
    assert "first" not in repr(first)
    assert "test-secret" not in repr(provider)


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"VersionId": "", "SecretString": "sensitive"},
        {"VersionId": "v", "SecretBinary": b"sensitive"},
        {"VersionId": "v", "SecretString": 42},
        {"VersionId": "v", "SecretString": "malformed-sensitive-json"},
    ],
)
def test_invalid_refresh_never_extends_expired_cache(response, monkeypatch):
    now = [0]
    monkeypatch.setattr("datus_db_core.secrets.time.monotonic", lambda: now[0])
    identity = Mock()
    identity.client.return_value.get_secret_value.side_effect = [
        {"VersionId": "v1", "SecretString": '{"password":"old"}'},
        response,
        response,
    ]
    provider = AWSSecretProvider(identity, "secret", 10, json.loads)
    provider.load()
    now[0] = 10
    for _ in range(2):
        with pytest.raises(DatusDbException, match="Invalid database Secret") as error:
            provider.load()
        assert "sensitive" not in str(error.value)
        assert error.value.__suppress_context__
    assert identity.client.return_value.get_secret_value.call_count == 3


def test_concurrent_acquisition_reads_one_snapshot():
    barrier = threading.Barrier(8)
    identity = Mock()
    identity.client.return_value.get_secret_value.return_value = {"VersionId": "v1", "SecretString": "credential"}
    provider = AWSSecretProvider(identity, "secret", 300, str)

    def acquire():
        barrier.wait(timeout=5)
        return provider.load()

    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(pool.map(lambda _: acquire(), range(8)))
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    identity.client.return_value.get_secret_value.assert_called_once_with(SecretId="secret", VersionStage="AWSCURRENT")


def test_sdk_missing_or_denied_is_actionable_and_redacted():
    with patch.dict("sys.modules", {"boto3": None}):
        with pytest.raises(DatusDbException, match=r"\[aws\] extra"):
            AWSSecretProvider(AWSIdentity(), "secret", 300, str).load()
    identity = Mock()
    identity.client.return_value.get_secret_value.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "sensitive-value"}}, "GetSecretValue"
    )
    with pytest.raises(DatusDbException, match="AccessDeniedException") as error:
        AWSSecretProvider(identity, "secret", 300, str).load()
    assert "sensitive-value" not in str(error.value)


@pytest.mark.parametrize("reference,ttl", [("", 1), ("s", 0), ("s", 86401)])
def test_provider_requires_reference_and_bounded_ttl(reference, ttl):
    with pytest.raises(ValueError):
        AWSSecretProvider(Mock(), reference, ttl, str)
