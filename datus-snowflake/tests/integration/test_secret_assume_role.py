# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live single-account simulation of a third-party Secret reader.

| Environment | Purpose |
| --- | --- |
| DATUS_SECRET_ROLE_TESTS=1 | Allow two temporary IAM roles and two owned Secrets |
| DATUS_SECRET_AWS_REGION | Secrets region; default us-east-1 |
| DATUS_SECRET_AWS_PROFILE | Provisioning profile; default SDK profile |
| DATUS_SNOWFLAKE_SECRET_TESTS=1 | Also test a real Snowflake login through the role chain |
| SNOWFLAKE_* | Existing key-pair credentials from integration/conftest.py |

Uses real STS/IAM/Secrets Manager APIs. Both roles belong to the same account;
this does not validate a second account's trust/SCP/KMS boundary. The existing
AWS configuration is left untouched. Only the temporary resources are deleted.
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

from datus_db_core import DatusDbException
from datus_db_core.aws import AWSIdentity, AWSIdentityConfig
from datus_db_core.secrets import AWSSecretProvider
from datus_snowflake import SnowflakeConfig, SnowflakeConnector
from datus_snowflake.auth import SnowflakeCredentials

from ._aws_helpers import delete_role as _delete_role
from ._aws_helpers import delete_secret as _delete_secret
from ._aws_helpers import temporary_profiles, temporary_secret_value

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("DATUS_SECRET_ROLE_TESTS") != "1", reason="DATUS_SECRET_ROLE_TESTS=1 is required"),
]


def _wait_for_role_chain(sts, source_arn, reader_arn, external_id, sdk_config):
    # IAM policy propagation is an external setup boundary. Do not retry errors
    # in the actual denial tests or any Snowflake business SQL.
    for _ in range(40):
        try:
            temporary = sts.assume_role(RoleArn=source_arn, RoleSessionName="datus-role-setup")["Credentials"]
            with closing(
                boto3.client(
                    "sts",
                    region_name=sts.meta.region_name,
                    aws_access_key_id=temporary["AccessKeyId"],
                    aws_secret_access_key=temporary["SecretAccessKey"],
                    aws_session_token=temporary["SessionToken"],
                    config=sdk_config,
                )
            ) as source:
                source.assume_role(RoleArn=reader_arn, RoleSessionName="datus-reader-setup", ExternalId=external_id)
            return
        except ClientError as error:
            if error.response["Error"]["Code"] != "AccessDenied":
                raise
            time.sleep(1)
    pytest.fail("Temporary IAM role chain did not become available")


@pytest.fixture(scope="module")
def role_lab(tmp_path_factory):
    run_id = uuid.uuid4().hex
    region = os.getenv("DATUS_SECRET_AWS_REGION", "us-east-1")
    profile = os.getenv("DATUS_SECRET_AWS_PROFILE") or "default"
    session = boto3.Session(profile_name=profile, region_name=region)
    sdk_config = Config(connect_timeout=5, read_timeout=15, retries={"mode": "standard", "total_max_attempts": 2})
    tags = {"datus-test-run": run_id, "purpose": "cmp-secret-01-role-simulation"}
    tag_list = [{"Key": key, "Value": value} for key, value in tags.items()]
    external_id = f"datus-customer-{run_id}"

    with ExitStack() as cleanup:
        iam = cleanup.enter_context(closing(session.client("iam", config=sdk_config)))
        sts = cleanup.enter_context(closing(session.client("sts", config=sdk_config)))
        secrets_client = cleanup.enter_context(closing(session.client("secretsmanager", config=sdk_config)))
        caller = sts.get_caller_identity()
        principal = caller["Arn"]
        if ":assumed-role/" in principal:
            role_name = principal.split(":assumed-role/", 1)[1].split("/", 1)[0]
            principal = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        source_name, reader_name = f"DatusSecretSource{run_id}", f"DatusSecretReader{run_id}"

        source_arn = iam.create_role(
            RoleName=source_name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow", "Principal": {"AWS": principal}, "Action": "sts:AssumeRole"}],
                }
            ),
            Tags=tag_list,
        )["Role"]["Arn"]
        cleanup.callback(_delete_role, iam, source_name, tags)
        partition = source_arn.split(":")[1]
        reader_arn = iam.create_role(
            RoleName=reader_name,
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"AWS": f"arn:{partition}:iam::{caller['Account']}:root"},
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {"aws:PrincipalArn": source_arn, "sts:ExternalId": external_id}
                            },
                        }
                    ],
                }
            ),
            Tags=tag_list,
        )["Role"]["Arn"]
        cleanup.callback(_delete_role, iam, reader_name, tags)
        arns = []
        for suffix in ("allowed", "denied"):
            arn = secrets_client.create_secret(
                Name=f"datus/cmp-secret-01/roles-{run_id}-{suffix}",
                SecretString='{"login":"reader","password":"role-test-value"}',
                Tags=tag_list,
            )["ARN"]
            cleanup.callback(_delete_secret, secrets_client, arn, tags)
            arns.append(arn)
        iam.put_role_policy(
            RoleName=source_name,
            PolicyName="AssumeReaderOnly",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole", "Resource": reader_arn}],
                }
            ),
        )
        iam.put_role_policy(
            RoleName=reader_name,
            PolicyName="ReadOneSecretOnly",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [{"Effect": "Allow", "Action": "secretsmanager:GetSecretValue", "Resource": arns[0]}],
                }
            ),
        )
        _wait_for_role_chain(sts, source_arn, reader_arn, external_id, sdk_config)

        profiles = {
            "source": {"role_arn": source_arn, "source_profile": profile},
            "reader": {"role_arn": reader_arn, "source_profile": f"cmp-source-{run_id}", "external_id": external_id},
            "wrong": {
                "role_arn": reader_arn,
                "source_profile": f"cmp-source-{run_id}",
                "external_id": "wrong-customer",
            },
            "missing": {"role_arn": reader_arn, "source_profile": f"cmp-source-{run_id}"},
            "untrusted": {"role_arn": reader_arn, "source_profile": profile, "external_id": external_id},
        }
        with temporary_profiles(tmp_path_factory.mktemp("secret-role-profiles"), region, run_id, profiles) as names:
            yield SimpleNamespace(
                region=region,
                profiles=names,
                source_arn=source_arn,
                reader_arn=reader_arn,
                account=caller["Account"],
                secret_arn=arns[0],
                denied_arn=arns[1],
                secrets_client=secrets_client,
            )


def test_profile_chain_reads_mapped_credentials_under_reader_identity(role_lab):
    credentials = SnowflakeCredentials(
        SnowflakeConfig(
            account="test-account",
            warehouse="test-warehouse",
            auth_mode="password",
            secret_ref=role_lab.secret_arn,
            secret_key_mapping={"username": "login", "password": "password"},
            aws={"profile": role_lab.profiles["reader"], "region": role_lab.region},
        )
    )
    try:
        snapshot = credentials.provider.load(force=True)
        assert snapshot.value.connection_parameters() == {"user": "reader", "password": "role-test-value"}
        caller = credentials.identity.client("sts").get_caller_identity()
        assert caller["Account"] == role_lab.account
        assert ":assumed-role/" + role_lab.reader_arn.rsplit("/", 1)[1] + "/" in caller["Arn"]
    finally:
        credentials.identity.close()


@pytest.mark.parametrize("profile", ["source", "wrong", "missing", "untrusted"])
def test_role_or_external_id_cannot_bypass_authorization(role_lab, profile):
    identity = AWSIdentity(AWSIdentityConfig(profile=role_lab.profiles[profile], region=role_lab.region))
    try:
        with pytest.raises(DatusDbException, match="AccessDenied"):
            AWSSecretProvider(identity, role_lab.secret_arn, 300, str).load()
    finally:
        identity.close()


def test_reader_cannot_read_another_existing_secret(role_lab):
    identity = AWSIdentity(AWSIdentityConfig(profile=role_lab.profiles["reader"], region=role_lab.region))
    try:
        with pytest.raises(DatusDbException, match="AccessDenied"):
            AWSSecretProvider(identity, role_lab.denied_arn, 300, str).load()
    finally:
        identity.close()


@pytest.mark.skipif(os.getenv("DATUS_SNOWFLAKE_SECRET_TESTS") != "1", reason="Snowflake key-pair login is opt-in")
def test_role_profile_secret_reaches_real_snowflake_login(role_lab):
    required = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_PRIVATE_KEY_FILE")
    assert all(os.getenv(key) for key in required), "Snowflake key-pair environment is required"
    payload = {"login": os.environ["SNOWFLAKE_USER"], "pem": Path(os.environ["SNOWFLAKE_PRIVATE_KEY_FILE"]).read_text()}
    mapping = {"username": "login", "private_key": "pem"}
    if os.getenv("SNOWFLAKE_PRIVATE_KEY_FILE_PWD"):
        payload["passphrase"] = os.environ["SNOWFLAKE_PRIVATE_KEY_FILE_PWD"]
        mapping["private_key_file_pwd"] = "passphrase"
    config = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "auth_mode": "key_pair",
        "secret_ref": role_lab.secret_arn,
        "secret_key_mapping": mapping,
        "aws": {"profile": role_lab.profiles["reader"], "region": role_lab.region},
    }
    with temporary_secret_value(role_lab.secrets_client, role_lab.secret_arn, json.dumps(payload)):
        connector = SnowflakeConnector(config)
        try:
            result = connector.execute_query('SELECT CURRENT_USER() AS "user", 1 AS "value"', result_format="list")
            assert result.success is True
            assert result.sql_return == [{"user": os.environ["SNOWFLAKE_USER"], "value": 1}]
        finally:
            connector.close()
