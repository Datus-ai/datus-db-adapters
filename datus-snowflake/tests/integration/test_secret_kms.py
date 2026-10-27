# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live customer-managed KMS acceptance in one AWS account.

| Environment | Purpose |
| --- | --- |
| DATUS_SECRET_KMS_TESTS=1 | Create one KMS key, two Secrets and four restricted roles |
| DATUS_SECRET_AWS_REGION | Test region; default us-east-1 |
| DATUS_SECRET_AWS_PROFILE | Provisioning profile; default SDK profile |
| DATUS_SNOWFLAKE_SECRET_TESTS=1 | Also perform a real key-pair Snowflake login |
| SNOWFLAKE_* | Existing key-pair configuration from integration/conftest.py |

All operations use real AWS APIs. The control Secret distinguishes permission
to read Secrets from permission to decrypt the customer-managed key. Each probe
forces a fresh read. Cleanup schedules ONLY the owned KMS key for deletion with
the minimum seven-day waiting period; JUnit properties record its ID and date.
"""

import json
import os
import time
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from datus_db_core import DatusDbException, ErrorCode
from datus_db_core.aws import AWSIdentity, AWSIdentityConfig
from datus_db_core.secrets import AWSSecretProvider
from datus_snowflake import SnowflakeConnector

from ._aws_helpers import delete_role, delete_secret, temporary_profiles, temporary_secret_value

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("DATUS_SECRET_KMS_TESTS") != "1", reason="DATUS_SECRET_KMS_TESTS=1 is required"),
]


def _schedule_owned_key_deletion(kms, key_id, tags, record):
    actual = {item["TagKey"]: item["TagValue"] for item in kms.list_resource_tags(KeyId=key_id)["Tags"]}
    assert tags.items() <= actual.items(), "Refusing to delete a KMS key without matching ownership"
    kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)
    for _ in range(60):
        metadata = kms.describe_key(KeyId=key_id)["KeyMetadata"]
        if metadata["KeyState"] == "PendingDeletion":
            assert metadata["Enabled"] is False
            record("kms_key_state", metadata["KeyState"])
            record("kms_key_deletion_date", metadata["DeletionDate"].isoformat())
            return
        time.sleep(0.5)
    pytest.fail("Owned KMS key was not scheduled for deletion")


def _wait_success(operation):
    for _ in range(40):
        try:
            return operation()
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"AccessDenied", "AccessDeniedException", "DecryptionFailure"}:
                raise
            time.sleep(1)
    pytest.fail("AWS permission or key-state change did not propagate")


def _fresh_read(lab, label):
    return AWSSecretProvider(lab.identities[label], lab.secret_arn, 300, json.loads).load(force=True)


def _expect_denied_read(lab, label, *, kms_failure):
    client = lab.identities[label].client("secretsmanager")
    for _ in range(40):
        try:
            client.get_secret_value(SecretId=lab.secret_arn, VersionStage="AWSCURRENT")
        except ClientError as error:
            code = error.response["Error"]["Code"]
            assert code in {"AccessDeniedException", "DecryptionFailure"}
            message = error.response["Error"]["Message"].lower()
            if kms_failure:
                assert "kms" in message or "decrypt" in message
            else:
                assert "secretsmanager:getsecretvalue" in message
            break
        time.sleep(1)
    else:
        pytest.fail("Restricted Secret read unexpectedly remained authorized")
    with pytest.raises(DatusDbException, match="AccessDenied|DecryptionFailure") as error:
        _fresh_read(lab, label)
    assert error.value.code is ErrorCode.DB_AUTHENTICATION_FAILED
    assert error.value.__suppress_context__


@pytest.fixture(scope="module")
def kms_lab(tmp_path_factory, record_testsuite_property):
    run_id = uuid.uuid4().hex
    region = os.getenv("DATUS_SECRET_AWS_REGION", "us-east-1")
    profile = os.getenv("DATUS_SECRET_AWS_PROFILE") or "default"
    session = boto3.Session(profile_name=profile, region_name=region)
    sdk_config = Config(connect_timeout=5, read_timeout=15, retries={"mode": "standard", "total_max_attempts": 2})
    tags = {"datus-test-run": run_id, "purpose": "cmp-secret-01-kms"}
    tag_list = [{"Key": key, "Value": value} for key, value in tags.items()]
    record_testsuite_property("kms_test_run", run_id)
    with ExitStack() as cleanup:
        iam = cleanup.enter_context(closing(session.client("iam", config=sdk_config)))
        sts = cleanup.enter_context(closing(session.client("sts", config=sdk_config)))
        kms = cleanup.enter_context(closing(session.client("kms", config=sdk_config)))
        sm = cleanup.enter_context(closing(session.client("secretsmanager", config=sdk_config)))
        caller = sts.get_caller_identity()
        principal = caller["Arn"]
        if ":assumed-role/" in principal:
            role_name = principal.split(":assumed-role/", 1)[1].split("/", 1)[0]
            principal = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        key = kms.create_key(
            Description=f"Datus CMP-SECRET-01 disposable KMS acceptance {run_id}",
            KeyUsage="ENCRYPT_DECRYPT",
            KeySpec="SYMMETRIC_DEFAULT",
            MultiRegion=False,
            Tags=[{"TagKey": key, "TagValue": value} for key, value in tags.items()],
        )["KeyMetadata"]
        cleanup.callback(_schedule_owned_key_deletion, kms, key["KeyId"], tags, record_testsuite_property)
        record_testsuite_property("kms_key_id", key["KeyId"])
        arns = []
        for label in ("encrypted", "control"):
            options = {"KmsKeyId": key["Arn"]} if label == "encrypted" else {}
            created = sm.create_secret(
                Name=f"datus/cmp-secret-01/kms-{run_id}-{label}",
                Tags=tag_list,
                SecretString='{"login":"reader","password":"kms-test-value"}',
                **options,
            )
            arns.append(created["ARN"])
            cleanup.callback(delete_secret, sm, created["ARN"], tags, record_testsuite_property)
        record_testsuite_property("kms_secret_arns", json.dumps(arns))
        role_arns = {}
        for label in ("allowed", "no_decrypt", "no_secret", "key_denied"):
            name = f"DatusKmsSecret{label.replace('_', '')}{run_id}"
            role_arns[label] = iam.create_role(
                RoleName=name,
                Tags=tag_list,
                AssumeRolePolicyDocument=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Principal": {"AWS": principal},
                                "Action": "sts:AssumeRole",
                            }
                        ],
                    }
                ),
            )["Role"]["Arn"]
            cleanup.callback(delete_role, iam, name, tags)
            statements = []
            if label != "no_secret":
                statements.append({"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": arns})
            if label != "no_decrypt":
                statements.append(
                    {
                        "Effect": "Allow",
                        "Action": "kms:Decrypt",
                        "Resource": key["Arn"],
                        "Condition": {
                            "StringEquals": {
                                "kms:ViaService": f"secretsmanager.{region}.amazonaws.com",
                                "kms:EncryptionContext:SecretARN": arns[0],
                            }
                        },
                    }
                )
            iam.put_role_policy(
                RoleName=name,
                PolicyName="ReadTestSecret",
                PolicyDocument=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": statements,
                    }
                ),
            )
        record_testsuite_property("kms_role_arns", json.dumps(role_arns))
        policy = json.loads(kms.get_key_policy(KeyId=key["KeyId"], PolicyName="default")["Policy"])
        policy["Statement"].append(
            {
                "Sid": "DenySpecificTestReader",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "kms:Decrypt",
                "Resource": "*",
                "Condition": {"ArnEquals": {"aws:PrincipalArn": role_arns["key_denied"]}},
            }
        )
        kms.put_key_policy(KeyId=key["KeyId"], PolicyName="default", Policy=json.dumps(policy))
        profiles = {label: {"role_arn": arn, "source_profile": profile} for label, arn in role_arns.items()}
        with temporary_profiles(tmp_path_factory.mktemp("secret-kms-profiles"), region, run_id, profiles) as names:
            identities = {}
            for label, name in names.items():
                identity = AWSIdentity(AWSIdentityConfig(profile=name, region=region), client_config=sdk_config)
                cleanup.callback(identity.close)
                identities[label] = identity
                resolved = _wait_success(lambda identity=identity: identity.client("sts").get_caller_identity())
                assert resolved["Account"] == caller["Account"]
                if label != "no_secret":
                    client = identity.client("secretsmanager")
                    control = _wait_success(lambda client=client: client.get_secret_value(SecretId=arns[1]))
                    assert json.loads(control["SecretString"]) == {"login": "reader", "password": "kms-test-value"}
            client = identities["allowed"].client("secretsmanager")
            _wait_success(lambda: client.get_secret_value(SecretId=arns[0]))
            yield SimpleNamespace(
                kms=kms,
                sm=sm,
                key=key,
                secret_arn=arns[0],
                control_arn=arns[1],
                identities=identities,
                profiles=names,
                region=region,
            )


def test_customer_managed_key_decrypts_secret_through_adapter(kms_lab):
    assert kms_lab.sm.describe_secret(SecretId=kms_lab.secret_arn)["KmsKeyId"] == kms_lab.key["Arn"]
    assert _fresh_read(kms_lab, "allowed").value == {"login": "reader", "password": "kms-test-value"}


@pytest.mark.parametrize("label", ["no_decrypt", "no_secret", "key_denied"])
def test_secret_and_kms_permissions_are_enforced_independently(kms_lab, label):
    _expect_denied_read(kms_lab, label, kms_failure=label != "no_secret")


@pytest.mark.skipif(os.getenv("DATUS_SNOWFLAKE_SECRET_TESTS") != "1", reason="Snowflake key-pair login is opt-in")
def test_kms_encrypted_private_key_reaches_real_snowflake(kms_lab):
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_PRIVATE_KEY_FILE")
    assert all(os.getenv(key) for key in required), "Snowflake key-pair configuration is required"
    payload = {"login": os.environ["SNOWFLAKE_USER"], "pem": Path(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]).read_text()}
    mapping = {"username": "login", "private_key": "pem"}
    if os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE_PWD"):
        payload["passphrase"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE_PWD"]
        mapping["private_key_file_pwd"] = "passphrase"
    config = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "auth_mode": "key_pair",
        "secret_ref": kms_lab.secret_arn,
        "secret_key_mapping": mapping,
        "aws": {"profile": kms_lab.profiles["allowed"], "region": kms_lab.region},
    }
    with temporary_secret_value(kms_lab.sm, kms_lab.secret_arn, json.dumps(payload)):
        connector = SnowflakeConnector(config)
        try:
            result = connector.execute_query('SELECT CURRENT_USER() AS "user", 1 AS "value"', result_format="list")
            assert result.success is True
            assert result.sql_return == [{"user": os.environ["SNOWFLAKE_USER"], "value": 1}]
        finally:
            connector.close()


def test_disabled_key_blocks_fresh_secret_reads_and_reenable_restores_access(kms_lab):
    before = _fresh_read(kms_lab, "allowed")
    kms_lab.kms.disable_key(KeyId=kms_lab.key["KeyId"])
    try:
        _expect_denied_read(kms_lab, "allowed", kms_failure=True)
    finally:
        kms_lab.kms.enable_key(KeyId=kms_lab.key["KeyId"])
        client = kms_lab.identities["allowed"].client("secretsmanager")
        _wait_success(lambda: client.get_secret_value(SecretId=kms_lab.secret_arn))
    assert _fresh_read(kms_lab, "allowed").version == before.version
