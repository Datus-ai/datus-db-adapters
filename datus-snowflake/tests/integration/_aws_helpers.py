# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Ownership-scoped cleanup and isolated profiles for live AWS acceptance."""

import os
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def publish_secret(client, arn, raw):
    """Wait for AWS propagation before testing the adapter's separate cache."""
    version = client.put_secret_value(SecretId=arn, SecretString=raw)["VersionId"]
    for _ in range(60):
        if client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")["VersionId"] == version:
            return version
        time.sleep(0.5)
    pytest.fail("Published Secret version did not become visible as AWSCURRENT")


@contextmanager
def temporary_secret_value(client, arn, raw):
    original = client.get_secret_value(SecretId=arn, VersionStage="AWSCURRENT")["SecretString"]
    try:
        yield publish_secret(client, arn, raw)
    finally:
        publish_secret(client, arn, original)


def delete_secret(client, arn, tags):
    actual = {item["Key"]: item["Value"] for item in client.describe_secret(SecretId=arn)["Tags"]}
    assert actual == tags
    client.delete_secret(SecretId=arn, ForceDeleteWithoutRecovery=True)
    for _ in range(60):
        try:
            client.describe_secret(SecretId=arn)
        except client.exceptions.ResourceNotFoundException:
            return
        time.sleep(0.5)
    pytest.fail("Owned test Secret remains after deletion")


def delete_role(client, name, tags):
    actual = {item["Key"]: item["Value"] for item in client.list_role_tags(RoleName=name)["Tags"]}
    assert actual == tags
    for policy in client.list_role_policies(RoleName=name)["PolicyNames"]:
        client.delete_role_policy(RoleName=name, PolicyName=policy)
    client.delete_role(RoleName=name)
    for _ in range(60):
        try:
            client.get_role(RoleName=name)
        except client.exceptions.NoSuchEntityException:
            return
        time.sleep(0.5)
    pytest.fail("Owned test role remains after deletion")


@contextmanager
def temporary_profiles(directory, region, run_id, profiles):
    config_path = directory / "config"
    original = Path(os.environ.get("AWS_CONFIG_FILE", str(Path.home() / ".aws" / "config"))).expanduser()
    base_config = original.read_text() if original.exists() else ""
    descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(base_config + "\n")
            for label, values in profiles.items():
                handle.write(f"\n[profile cmp-{label}-{run_id}]\nregion = {region}\n")
                for key, value in values.items():
                    handle.write(f"{key} = {value}\n")
        with pytest.MonkeyPatch.context() as environment:
            environment.setenv("AWS_CONFIG_FILE", str(config_path))
            yield {label: f"cmp-{label}-{run_id}" for label in profiles}
    finally:
        config_path.unlink(missing_ok=True)
