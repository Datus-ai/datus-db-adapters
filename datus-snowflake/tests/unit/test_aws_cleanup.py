# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import json
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.integration._aws_helpers import delete_role, delete_secret
from tests.integration.test_secret_kms import _schedule_owned_key_deletion

TAGS = {"datus-test-run": "run", "purpose": "cmp-secret-01"}
DELETION_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ResourceNotFound(Exception):
    pass


@pytest.mark.parametrize("resource", ["secret", "role", "kms"])
@pytest.mark.parametrize("ownership", [TAGS, TAGS | {"datus-test-run": "another-run"}, {"datus-test-run": "run"}])
def test_cleanup_allows_account_tags_but_requires_every_ownership_tag(resource, ownership):
    client, record = Mock(), Mock()
    tags = ownership | {"cost-center": "automation"}
    client.exceptions = SimpleNamespace(
        ResourceNotFoundException=ResourceNotFound, NoSuchEntityException=ResourceNotFound
    )
    tag_list = [{"Key": key, "Value": value} for key, value in tags.items()]
    if resource == "secret":
        client.describe_secret.side_effect = [{"Tags": tag_list}, ResourceNotFound()]
        client.delete_secret.return_value = {"ARN": "arn", "DeletionDate": DELETION_DATE}
        operation = partial(delete_secret, client, "arn", TAGS, record)
        deletion = client.delete_secret
        expected = {"SecretId": "arn", "ForceDeleteWithoutRecovery": True}
    elif resource == "role":
        client.list_role_tags.return_value = {"Tags": tag_list}
        client.list_role_policies.return_value = {"PolicyNames": []}
        client.get_role.side_effect = ResourceNotFound()
        operation = partial(delete_role, client, "role", TAGS)
        deletion = client.delete_role
        expected = {"RoleName": "role"}
    else:
        client.list_resource_tags.return_value = {
            "Tags": [{"TagKey": key, "TagValue": value} for key, value in tags.items()]
        }
        client.describe_key.return_value = {
            "KeyMetadata": {"KeyState": "PendingDeletion", "Enabled": False, "DeletionDate": DELETION_DATE}
        }
        operation = partial(_schedule_owned_key_deletion, client, "key", TAGS, record)
        deletion = client.schedule_key_deletion
        expected = {"KeyId": "key", "PendingWindowInDays": 7}
    if ownership != TAGS:
        with pytest.raises(AssertionError, match="matching ownership"):
            operation()
        deletion.assert_not_called()
    else:
        operation()
        deletion.assert_called_once_with(**expected)


@pytest.mark.parametrize("absent", [False, True])
def test_secret_cleanup_records_deletion_acceptance_without_a_physical_removal_deadline(absent):
    client, record = Mock(), Mock()
    client.exceptions = SimpleNamespace(ResourceNotFoundException=ResourceNotFound)
    client.describe_secret.side_effect = [
        {"Tags": [{"Key": key, "Value": value} for key, value in TAGS.items()]},
        ResourceNotFound() if absent else {"ARN": "arn"},
    ]
    client.delete_secret.return_value = {"ARN": "arn", "DeletionDate": DELETION_DATE}
    delete_secret(client, "arn", TAGS, record)
    record.assert_called_once_with(
        "aws_secret_cleanup",
        json.dumps(
            {
                "arn": "arn",
                "state": "deleted" if absent else "deletion_requested",
                "deletion_date": DELETION_DATE.isoformat(),
            }
        ),
    )


def test_delete_api_failure_is_not_recorded_as_cleanup_success():
    client, record = Mock(), Mock()
    client.describe_secret.return_value = {"Tags": [{"Key": key, "Value": value} for key, value in TAGS.items()]}
    client.delete_secret.side_effect = RuntimeError("DeleteSecret denied")
    with pytest.raises(RuntimeError, match="DeleteSecret denied"):
        delete_secret(client, "arn", TAGS, record)
    record.assert_not_called()
