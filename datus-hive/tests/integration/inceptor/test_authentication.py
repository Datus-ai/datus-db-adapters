"""Strict password-authentication checks for manually configured LDAP/CUSTOM endpoints."""

import os

import pytest

from datus_hive import HiveConfig, HiveConnector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("HIVE_AUTH") not in {"LDAP", "CUSTOM"}, reason="Requires LDAP/CUSTOM endpoint"),
]


@pytest.mark.parametrize(
    "override",
    [
        {"password": "datus_definitely_wrong_password"},
        {"username": "datus_nonexistent_user"},
        {"auth": "NONE", "password": ""},
        {"auth": None, "password": ""},
    ],
)
def test_password_authentication_rejects_invalid_credentials(connector, override):
    config = HiveConfig(
        host=os.getenv("HIVE_HOST", "localhost"),
        port=int(os.getenv("HIVE_PORT", "19110")),
        username=os.getenv("HIVE_USERNAME", "hive"),
        password=os.environ["HIVE_PASSWORD"],
        auth=os.environ["HIVE_AUTH"],
        database="default",
    ).model_copy(update=override)
    connection = HiveConnector(config)
    try:
        with pytest.raises(Exception, match="(?i)auth|credential|password|SASL|PLAIN|mechanism|transport|TSocket"):
            connection.get_databases()
    finally:
        connection.close()
