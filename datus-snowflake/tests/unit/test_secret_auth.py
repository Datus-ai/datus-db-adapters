# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from datus_db_core import DatusDbException
from datus_snowflake import SnowflakeConfig, SnowflakeConnector


@pytest.fixture(scope="module")
def key_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "pem": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        "encrypted": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"passphrase"),
        ).decode(),
        "der": key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
    }


@pytest.fixture
def factory(monkeypatch):
    sdk = Mock()
    sdk.return_value.client.return_value.get_secret_value.return_value = {"VersionId": "v1", "SecretString": "first"}
    monkeypatch.setattr("boto3.Session", sdk)
    connect = Mock(return_value=MagicMock())
    connect.return_value.is_closed.return_value = False
    monkeypatch.setattr("datus_snowflake.connector.Connect", connect)
    created = []

    def make(raw="first", **overrides):
        client = sdk.return_value.client.return_value
        client.get_secret_value.return_value = {"VersionId": "v1", "SecretString": raw}
        config = {
            "account": "account",
            "username": "reader",
            "warehouse": "warehouse",
            "secret_ref": "secret",
            "auth_mode": "password",
        }
        config.update(overrides)
        connector = SnowflakeConnector(config)
        created.append(connector)
        return connector, client, connect, sdk

    yield make
    for connector in created:
        connector.close()


@pytest.mark.parametrize("mapped", [False, True])
def test_password_source_reaches_native_login_with_fixed_connection_target(factory, mapped):
    raw = (
        json.dumps({"db_password": "credential", "unrelated": "ignored", "account": "cannot-override"})
        if mapped
        else "credential"
    )
    config = {"secret_key_mapping": {"password": "db_password"}} if mapped else {}
    _, client, connect, sdk = factory(
        raw, role="ANALYST", aws={"profile": "role-profile", "region": "us-east-1"}, **config
    )
    assert connect.call_args.kwargs == {
        "account": "account",
        "user": "reader",
        "warehouse": "warehouse",
        "database": None,
        "schema": None,
        "role": "ANALYST",
        "password": "credential",
        "login_timeout": 30,
        "network_timeout": 30,
        "socket_timeout": 30,
    }
    sdk.assert_called_once_with(profile_name="role-profile", region_name="us-east-1")
    sdk.return_value.get_credentials.assert_not_called()
    client.get_secret_value.assert_called_once_with(SecretId="secret", VersionStage="AWSCURRENT")


@pytest.mark.parametrize("encrypted", [False, True])
def test_private_key_mapping_decodes_to_der_before_login(factory, key_material, encrypted):
    raw = {"key": key_material["encrypted" if encrypted else "pem"], "user": "reader", "ignored": "not-forwarded"}
    mapping = {"private_key": "key", "username": "user"}
    if encrypted:
        raw["pass"] = "passphrase"
        mapping["private_key_file_pwd"] = "pass"
    connector, _, connect, _ = factory(json.dumps(raw), username=None, auth_mode="key_pair", secret_key_mapping=mapping)
    assert connect.call_args.kwargs["private_key"] == key_material["der"]
    assert connect.call_args.kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert connect.call_args.kwargs["user"] == "reader"
    assert "password" not in connect.call_args.kwargs
    snapshot = connector._credentials.provider.load()
    assert "passphrase" not in repr(snapshot)
    assert "PRIVATE KEY" not in repr(snapshot.value)


def test_raw_private_key_and_all_blank_mapping(factory, key_material):
    _, _, connect, _ = factory(
        key_material["pem"], auth_mode="key_pair", secret_key_mapping={"private_key": "", "username": " "}
    )
    assert connect.call_args.kwargs["private_key"] == key_material["der"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"auth_mode": None},
        {"auth_mode": "oauth"},
        {"secret_cache_ttl_seconds": 0},
        {"secret_cache_ttl_seconds": 301},
        {"password": "sensitive-value"},
        {"private_key": "sensitive-value"},
        {"private_key_file": "/key"},
        {"private_key_file_pwd": "sensitive-value"},
        {"username": None},
        {"secret_key_mapping": {"username": "user"}},
        {"secret_key_mapping": {"private_key": "key"}},
        {"secret_key_mapping": {"password": "pw", "private_key": "key"}},
        {"secret_key_mapping": {"password": "pw", "unknown": "sensitive-value"}},
        {"secret_ref": None, "secret_key_mapping": {"password": "pw"}},
        {"secret_ref": None, "aws": {"region": "us-east-1"}},
        {"secret_ref": None, "username": None, "password": "sensitive-value"},
        {"secret_ref": None, "private_key": "sensitive-value"},
        {"secret_ref": None, "auth_mode": "key_pair", "password": "sensitive-value"},
    ],
)
def test_invalid_secret_profiles_are_rejected_without_input_values(overrides):
    config = {"account": "a", "username": "reader", "warehouse": "w", "secret_ref": "secret", "auth_mode": "password"}
    config.update(overrides)
    with pytest.raises(ValidationError) as error:
        SnowflakeConfig(**config)
    assert "sensitive-value" not in str(error.value)


@pytest.mark.parametrize(
    "raw,mapping",
    [
        ('{"password":123}', {"password": "password"}),
        ('{"password":"sensitive-value","username":"wrong-user"}', {"password": "password", "username": "username"}),
        ('{"different":"sensitive-value"}', {"password": "password"}),
        ('["sensitive-value"]', {"password": "password"}),
        ("sensitive-value", {"password": "password"}),
    ],
)
def test_bad_payload_never_reaches_snowflake_or_leaks_secret(factory, raw, mapping, caplog):
    with pytest.raises(DatusDbException, match="Invalid database Secret") as error:
        factory(raw, secret_key_mapping=mapping)
    assert "sensitive-value" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__


def test_wrong_key_passphrase_is_redacted(factory, key_material, caplog):
    with pytest.raises(DatusDbException, match="Invalid database Secret"):
        factory(
            json.dumps({"key": key_material["encrypted"], "pass": "sensitive-wrong-passphrase"}),
            auth_mode="key_pair",
            secret_key_mapping={"private_key": "key", "private_key_file_pwd": "pass"},
        )
    assert "sensitive-wrong-passphrase" not in caplog.text


def test_cache_expiry_does_not_expire_session_and_reconnect_uses_new_secret(factory, monkeypatch):
    now = [0.0]
    monkeypatch.setattr("datus_db_core.secrets.time.monotonic", lambda: now[0])
    connector, client, connect, _ = factory(database="DB", schema="SCHEMA", role="ROLE", secret_cache_ttl_seconds=10)
    session = connector.connection
    now[0] = 11
    client.get_secret_value.return_value = {"VersionId": "v2", "SecretString": "second"}
    assert connector.test_connection()["success"] is True
    assert connector.connection is session
    session.close.assert_not_called()
    assert client.get_secret_value.call_count == 1
    connector.switch_context(database_name="NEXT_DB", schema_name="NEXT_SCHEMA")
    connector.close()
    connector.connect()
    assert connect.call_count == 2
    assert connect.call_args.kwargs["password"] == "second"
    assert connect.call_args.kwargs["database"] == "NEXT_DB"
    assert connect.call_args.kwargs["schema"] == "NEXT_SCHEMA"
    assert connect.call_args.kwargs["role"] == "ROLE"
    assert client.get_secret_value.call_count == 2


def test_reopen_inside_ttl_reuses_cached_credentials_and_force_refresh_keeps_session(factory):
    connector, client, connect, _ = factory()
    connector.close()
    connector.connect()
    assert client.get_secret_value.call_count == 1
    client.get_secret_value.return_value = {"VersionId": "v2", "SecretString": "second"}
    session = connector.connection
    calls = session.close.call_count
    assert connector.refresh_credentials() == "v2"
    assert session.close.call_count == calls
    assert connector.connection is session
    connector.close()
    connector.connect()
    assert connect.call_args.kwargs["password"] == "second"


def test_secret_username_is_bound_to_connector_identity(factory):
    connector, client, connect, _ = factory(
        '{"user":"reader","pw":"first"}', username=None, secret_key_mapping={"username": "user", "password": "pw"}
    )
    client.get_secret_value.return_value = {
        "VersionId": "v2",
        "SecretString": '{"user":"other","pw":"sensitive-value"}',
    }
    connector.close()
    with pytest.raises(DatusDbException, match="Invalid database Secret"):
        connector.refresh_credentials()
    assert connect.call_count == 1


def test_close_waits_for_active_cursor_and_does_not_replay_query(factory):
    connector, _, _, _ = factory()
    session = connector.connection
    cursor = session.cursor.return_value.__enter__.return_value
    entered, release, closing = threading.Event(), threading.Event(), threading.Event()

    def execute(sql):
        entered.set()
        assert release.wait(timeout=5)

    def close():
        closing.set()
        connector.close()

    cursor.execute.side_effect = execute
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(connector.test_connection)
        try:
            assert entered.wait(timeout=5)
            cleanup = pool.submit(close)
            assert closing.wait(timeout=5)
            session.close.assert_not_called()
        finally:
            release.set()
        assert query.result(timeout=5)["success"] is True
        cleanup.result(timeout=5)
    cursor.execute.assert_called_once_with("SELECT 1")
    session.close.assert_called_once_with()


def test_login_error_is_redacted_and_never_retries_business_sql(factory, caplog):
    connector, _, connect, _ = factory()
    connector.close()
    connect.side_effect = RuntimeError("sensitive-value")
    with pytest.raises(DatusDbException, match="Snowflake login failed") as error:
        connector.test_connection()
    assert "sensitive-value" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__
    assert connect.call_count == 2


def test_lost_session_requires_explicit_reconnect_before_business_sql(factory):
    connector, _, connect, _ = factory()
    lost = connector.connection
    lost.is_closed.return_value = True
    replacement = MagicMock()
    replacement.is_closed.return_value = False
    cursor = replacement.cursor.return_value.__enter__.return_value
    cursor.rowcount = 1
    cursor.sfqid = "new-session-query"
    connect.return_value = replacement
    sql = "INSERT INTO events VALUES (1)"

    result = connector.execute_insert(sql)

    assert result.success is False
    assert "explicitly" in result.error
    assert connect.call_count == 1
    lost.cursor.assert_not_called()
    replacement.cursor.assert_not_called()
    connector.connect()
    assert connector.execute_insert(sql).success is True
    assert connect.call_count == 2
    cursor.execute.assert_called_once_with(sql)


def test_failed_initial_login_closes_aws_clients(monkeypatch):
    sdk = Mock()
    client = sdk.return_value.client.return_value
    client.get_secret_value.return_value = {"VersionId": "v1", "SecretString": "test-credential"}
    monkeypatch.setattr("boto3.Session", sdk)
    connect = Mock(side_effect=RuntimeError("sensitive-value"))
    monkeypatch.setattr("datus_snowflake.connector.Connect", connect)
    with pytest.raises(DatusDbException, match="Snowflake login failed"):
        SnowflakeConnector(
            {"account": "a", "username": "reader", "warehouse": "w", "secret_ref": "secret", "auth_mode": "password"}
        )
    client.close.assert_called_once_with()
    connect.assert_called_once()
