# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Run through the real HTTPS endpoint provisioned by the isolated TLS runner."""

import os
from pathlib import Path

import pytest
from urllib3.exceptions import InsecureRequestWarning

from datus_trino import TrinoConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def tls_config(config):
    if config.http_scheme != "https":
        pytest.skip("Run ci.run_tls_integration_tests trino for the HTTPS endpoint")
    return config


def test_default_requests_trust_configuration(tls_config, monkeypatch):
    """Without ca_cert, the driver's normal Requests CA bundle setting still works."""
    if tls_config.ca_cert is not None:
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", tls_config.ca_cert)
    conn = TrinoConnector(tls_config.model_copy(update={"ca_cert": None}))
    try:
        result = conn.execute({"sql_query": "SELECT 22 AS value"}, result_format="list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 22}]
    finally:
        conn.close()


@pytest.mark.parametrize("fault", ["untrusted", "wrong-ca", "hostname"])
def test_certificate_verification_rejects_invalid_servers(tls_config, fault, monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    changes = {"ca_cert": None}
    if fault == "wrong-ca":
        changes["ca_cert"] = str(Path(os.environ["DATUS_TEST_TLS_DIR"]) / "wrong-ca.crt")
    elif fault == "hostname":
        changes = {"host": "127.0.0.1"}
    conn = TrinoConnector(tls_config.model_copy(update=changes))
    try:
        result = conn.execute({"sql_query": "SELECT 1 AS value"}, result_format="list")
        assert not result.success
        error = str(result.error).lower()
        assert "certificate" in error, result.error
        if fault == "hostname":
            assert "mismatch" in error or "not valid for" in error, result.error
    finally:
        conn.close()


def test_explicit_development_verify_override(tls_config):
    conn = TrinoConnector(tls_config.model_copy(update={"ca_cert": None, "verify": False}))
    try:
        with pytest.warns(InsecureRequestWarning):
            result = conn.execute({"sql_query": "SELECT 7 AS value"}, result_format="list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 7}]
    finally:
        conn.close()


def test_https_does_not_retry_using_plain_http(tls_config):
    conn = TrinoConnector(tls_config.model_copy(update={"port": int(os.environ["TRINO_PLAIN_PORT"])}))
    try:
        result = conn.execute({"sql_query": "SELECT 1 AS value"}, result_format="list")
        assert not result.success
        assert "ssl" in str(result.error).lower(), result.error
    finally:
        conn.close()


def test_tls_connection_can_be_reopened(tls_config):
    conn = TrinoConnector(tls_config)
    try:
        for _ in range(2):
            result = conn.execute({"sql_query": "SELECT '数据' AS value"}, result_format="list")
            assert result.success, result.error
            assert result.sql_return == [{"value": "数据"}]
            conn.close()
    finally:
        conn.close()
