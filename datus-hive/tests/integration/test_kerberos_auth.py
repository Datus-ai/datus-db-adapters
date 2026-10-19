# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Opt-in binary HS2 GSSAPI checks; a configured but broken endpoint fails.

Requires HIVE_AUTH=KERBEROS, HIVE_KERBEROS_SERVICE_NAME, and a valid ticket
cache. Connection and metadata suites cover the same endpoint's full contract.
"""

import os

import pytest

from datus_db_core.testing import contract
from datus_hive import HiveConfig, HiveConnector

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("HIVE_AUTH") != "KERBEROS", reason="Requires a Kerberos-configured HS2 endpoint"),
]


@pytest.fixture(scope="module")
def kerberos_config():
    config = HiveConfig(
        host=os.getenv("HIVE_HOST", "localhost"),
        port=int(os.getenv("HIVE_PORT", "10000")),
        username=os.getenv("HIVE_USERNAME", "hive"),
        database=os.getenv("HIVE_DATABASE", "default"),
        auth="KERBEROS",
        kerberos_service_name=os.environ["HIVE_KERBEROS_SERVICE_NAME"],
    )
    connection = HiveConnector(config)
    try:
        assert connection.test_connection(), "Kerberos endpoint must accept the configured ticket"
    finally:
        connection.close()
    return config


def test_kerberos_query_and_databases(kerberos_config):
    connection = HiveConnector(kerberos_config)
    try:
        result = connection.execute({"sql_query": "SELECT 1 AS kerberos_probe"}, result_format="list")
        contract.assert_success(result, "Kerberos query")
        assert result.sql_return == [{"kerberos_probe": 1}]
        assert kerberos_config.database in connection.get_databases()
    finally:
        connection.close()


def test_kerberos_identity_comes_from_ticket(kerberos_config):
    label = "datus_untrusted_client_label"
    results = []
    for config in (kerberos_config, kerberos_config.model_copy(update={"username": label})):
        connection = HiveConnector(config)
        try:
            result = connection.execute({"sql_query": "SELECT current_user() AS identity"}, result_format="list")
            contract.assert_success(result, "Kerberos identity query")
            results.append(result.sql_return)
        finally:
            connection.close()
    assert len(results[0]) == 1
    assert results[0][0]["identity"]
    assert results[0][0]["identity"] != label
    assert results[1] == results[0]


@pytest.mark.parametrize("auth", [None, "NONE"])
def test_plain_auth_rejected_by_kerberos_endpoint(kerberos_config, auth):
    config = kerberos_config.model_copy(update={"auth": auth, "kerberos_service_name": None})
    connection = HiveConnector(config)
    try:
        with pytest.raises(Exception, match="(?i)mechanism|authentication|PLAIN|GSS"):
            connection.get_databases()
    finally:
        connection.close()


def test_kerberos_wrong_service_rejected(kerberos_config):
    connection = HiveConnector(
        kerberos_config.model_copy(update={"kerberos_service_name": "datus_nonexistent_service"})
    )
    try:
        with pytest.raises(Exception, match="(?i)principal|server|GSS|Kerberos"):
            connection.get_databases()
    finally:
        connection.close()


def test_kerberos_missing_ticket_rejected(kerberos_config, monkeypatch, tmp_path):
    cache = tmp_path / "missing.ccache"
    assert not cache.exists()
    monkeypatch.setenv("KRB5CCNAME", f"FILE:{cache}")
    connection = HiveConnector(kerberos_config)
    try:
        with pytest.raises(Exception, match="(?i)credential|ticket|cache|GSS|Kerberos"):
            connection.get_databases()
    finally:
        connection.close()
