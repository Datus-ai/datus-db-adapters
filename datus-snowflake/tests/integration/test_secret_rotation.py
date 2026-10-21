# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Opt-in live AWS and Snowflake rotation acceptance.

| Environment | Purpose |
| --- | --- |
| DATUS_SECRET_AWS_TESTS=1 | Permit creating/updating/deleting an owned AWS Secret |
| DATUS_SECRET_AWS_REGION | AWS region (default us-east-1) |
| DATUS_SECRET_AWS_PROFILE | Optional SDK profile, including assume-role profiles |
| DATUS_SNOWFLAKE_SECRET_TESTS=1 | Permit temporary Snowflake user/role provisioning |
| SNOWFLAKE_* | Existing conftest credentials; role must manage users/roles/grants |

Only temporary users' keys are changed. Cleanup checks ownership and verifies absence.
AWS-only acceptance does not require Snowflake credentials. API failures never skip.
"""

import base64
import json
import os
import secrets
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from datus_db_core import DatusDbException
from datus_db_core.aws import AWSIdentity, AWSIdentityConfig
from datus_db_core.secrets import AWSSecretProvider
from datus_snowflake import SnowflakeConnector

from ._aws_helpers import publish_secret as _publish
from .conftest import _build_config_dict

pytestmark = pytest.mark.integration


@pytest.fixture
def aws_secret():
    if os.getenv("DATUS_SECRET_AWS_TESTS") != "1":
        pytest.skip("DATUS_SECRET_AWS_TESTS=1 is required")
    identity = AWSIdentity(
        AWSIdentityConfig(
            region=os.getenv("DATUS_SECRET_AWS_REGION", "us-east-1"),
            profile=os.getenv("DATUS_SECRET_AWS_PROFILE"),
        )
    )
    client = identity.client("secretsmanager")
    run_id = uuid.uuid4().hex
    created = client.create_secret(
        Name=f"datus/cmp-secret-01/{run_id}",
        SecretString='{"password":"initial-test-value"}',
        Tags=[{"Key": "datus-test-run", "Value": run_id}, {"Key": "purpose", "Value": "cmp-secret-01"}],
    )
    arn = created["ARN"]
    try:
        yield identity, client, arn, created["VersionId"], run_id
    finally:
        try:
            tags = {tag["Key"]: tag["Value"] for tag in client.describe_secret(SecretId=arn)["Tags"]}
            assert tags == {"datus-test-run": run_id, "purpose": "cmp-secret-01"}
            client.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)
            for _ in range(60):
                try:
                    client.describe_secret(SecretId=arn)
                except client.exceptions.ResourceNotFoundException:
                    break
                time.sleep(0.5)
            else:
                pytest.fail("Owned test Secret still exists after deletion")
        finally:
            identity.close()


def test_live_aws_cache_version_refresh_and_invalid_rotation(aws_secret):
    identity, client, arn, first_version, _ = aws_secret
    provider = AWSSecretProvider(identity, arn, 300, lambda raw: json.loads(raw)["password"])
    first = provider.load()
    assert (first.version, first.value) == (first_version, "initial-test-value")
    next_version = _publish(client, arn, '{"password":"rotated-test-value"}')
    assert provider.load() is first
    rotated = provider.load(force=True)
    assert (rotated.version, rotated.value) == (next_version, "rotated-test-value")
    expiring = AWSSecretProvider(identity, arn, 1, lambda raw: json.loads(raw)["password"])
    assert expiring.load().version == next_version
    _publish(client, arn, "invalid-json-test-value")
    time.sleep(1.05)
    with pytest.raises(DatusDbException, match="Invalid database Secret"):
        expiring.load()
    with pytest.raises(DatusDbException, match="Invalid database Secret"):
        expiring.load()
    restored = _publish(client, arn, '{"password":"restored-test-value"}')
    assert provider.load(force=True).version == restored


def _key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    passphrase = secrets.token_urlsafe(24)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase.encode()),
    ).decode()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    return pem, passphrase, public


@pytest.fixture
def rotation_user(aws_secret):
    if os.getenv("DATUS_SNOWFLAKE_SECRET_TESTS") != "1":
        pytest.skip("DATUS_SNOWFLAKE_SECRET_TESTS=1 is required")
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_PRIVATE_KEY_FILE")
    if not all(os.getenv(key) for key in required):
        pytest.skip("Snowflake admin key-pair configuration is required")
    _, _, _, _, run_id = aws_secret
    name = f"DATUS_SECRET_{run_id.upper()}"
    marker = f"datus-cmp-secret-01:{run_id}"
    warehouse = os.environ["SNOWFLAKE_WAREHOUSE"].replace('"', '""')
    keys = (_key_pair(), _key_pair())
    admin = SnowflakeConnector(_build_config_dict())

    def sql(statement):
        with admin.connection.cursor() as cursor:
            cursor.execute(statement)
            return cursor.fetchall()

    created = []
    try:
        sql(f"CREATE ROLE \"{name}\" COMMENT='{marker}'")
        created.append("ROLE")
        sql(
            f"CREATE USER \"{name}\" TYPE=SERVICE RSA_PUBLIC_KEY='{keys[0][2]}' "
            f"RSA_PUBLIC_KEY_2='{keys[1][2]}' DEFAULT_ROLE='{name}' COMMENT='{marker}'"
        )
        created.append("USER")
        sql(f'GRANT USAGE ON WAREHOUSE "{warehouse}" TO ROLE "{name}"')
        sql(f'GRANT ROLE "{name}" TO USER "{name}"')
        yield name, keys, sql
    finally:
        try:
            for kind in reversed(created):
                # Names are generated identifiers, never supplied by external input.
                with admin.connection.cursor() as cursor:
                    cursor.execute(f"SHOW {kind}S LIKE '{name}'")
                    columns = [item[0].lower() for item in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    owned = [row for row in rows if row["name"] == name]
                    assert len(owned) == 1 and owned[0]["comment"] == marker
                sql(f'DROP {kind} "{name}"')
                rows = sql(f"SHOW {kind}S LIKE '{name}'")
                assert rows == []
        finally:
            admin.close()


def test_live_snowflake_dual_key_rotation_keeps_session_and_reopens_with_current_secret(aws_secret, rotation_user):
    identity, client, arn, _, _ = aws_secret
    username, keys, admin_sql = rotation_user

    def publish(index):
        pem, passphrase, _ = keys[index]
        return _publish(
            client,
            arn,
            json.dumps({"login": username, "pem": pem, "passphrase": passphrase, "ignored": "extra"}),
        )

    first_version = publish(0)
    config = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "role": username,
        "auth_mode": "key_pair",
        "secret_ref": arn,
        "secret_key_mapping": {"username": "login", "private_key": "pem", "private_key_file_pwd": "passphrase"},
        "secret_cache_ttl_seconds": 1,
        "aws": identity.config.model_dump(exclude_none=True),
    }
    connector = SnowflakeConnector(config)

    def query():
        result = connector.execute_query(
            'SELECT CURRENT_USER() AS "user", CURRENT_ROLE() AS "role", '
            'CURRENT_WAREHOUSE() AS "warehouse", \'数据\' AS "text"',
            result_format="list",
        )
        assert result.success is True
        assert result.sql_return == [
            {"user": username, "role": username, "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"], "text": "数据"}
        ]

    try:
        query()
        assert connector._credentials.provider.load().version == first_version
        session = connector.connection
        with session.cursor() as cursor:
            cursor.execute("BEGIN")
            cursor.execute("SELECT CURRENT_TRANSACTION()")
            transaction = cursor.fetchone()[0]
        assert transaction is not None
        next_version = publish(1)
        waiting = connector.execute_query(
            'SELECT SYSTEM$WAIT(2) AS "waited", CURRENT_TRANSACTION() AS "transaction"', result_format="list"
        )
        assert waiting.success is True
        assert waiting.sql_return[0]["transaction"] == transaction
        query()
        assert connector.connection is session
        assert connector.refresh_credentials() == next_version
        assert connector.connection is session
        with session.cursor() as cursor:
            cursor.execute("SELECT CURRENT_TRANSACTION()")
            assert cursor.fetchone()[0] == transaction
            cursor.execute("ROLLBACK")
        admin_sql(f'ALTER USER "{username}" UNSET RSA_PUBLIC_KEY')
        connector.close()
        connector.connect()
        assert connector.connection is not session
        query()
        with pytest.raises(DatusDbException, match="Snowflake login failed"):
            SnowflakeConnector(
                {
                    "account": config["account"],
                    "username": username,
                    "warehouse": config["warehouse"],
                    "private_key": keys[0][0],
                    "private_key_file_pwd": keys[0][1],
                    "timeout_seconds": 10,
                }
            )
    finally:
        connector.close()
