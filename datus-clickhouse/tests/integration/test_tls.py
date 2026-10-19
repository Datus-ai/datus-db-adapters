# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Run through the real HTTPS endpoint provisioned by the isolated TLS runner."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from urllib3.exceptions import InsecureRequestWarning

from datus_clickhouse import ClickHouseConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def tls_config(config):
    if config.protocol != "https":
        pytest.skip("Run ci.run_tls_integration_tests clickhouse for the HTTPS endpoint")
    return config


@pytest.mark.parametrize("fault", ["untrusted", "wrong-ca", "hostname"])
def test_certificate_verification_rejects_invalid_servers(tls_config, fault, monkeypatch):
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    changes = {"ca_cert": None}
    if fault == "wrong-ca":
        changes["ca_cert"] = str(Path(os.environ["DATUS_TEST_TLS_DIR"]) / "wrong-ca.crt")
    elif fault == "hostname":
        changes = {"host": "127.0.0.1"}
    conn = ClickHouseConnector(tls_config.model_copy(update=changes))
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
    conn = ClickHouseConnector(tls_config.model_copy(update={"ca_cert": None, "verify": False}))
    try:
        with pytest.warns(InsecureRequestWarning):
            result = conn.execute({"sql_query": "SELECT 7 AS value"}, result_format="list")
        assert result.success, result.error
        assert result.sql_return == [{"value": 7}]
    finally:
        conn.close()


def test_https_does_not_retry_using_plain_http(tls_config):
    conn = ClickHouseConnector(tls_config.model_copy(update={"port": int(os.environ["CLICKHOUSE_PLAIN_PORT"])}))
    try:
        result = conn.execute({"sql_query": "SELECT 1 AS value"}, result_format="list")
        assert not result.success
        assert "ssl" in str(result.error).lower(), result.error
    finally:
        conn.close()


def test_tls_connection_can_be_reopened(tls_config):
    conn = ClickHouseConnector(tls_config)
    try:
        for _ in range(2):
            result = conn.execute({"sql_query": "SELECT '数据' AS value"}, result_format="list")
            assert result.success, result.error
            assert result.sql_return == [{"value": "数据"}]
            conn.close()
    finally:
        conn.close()


def test_setup_cli_retains_tls_for_database_creation(tls_config):
    database = f"tls_cli_{uuid.uuid4().hex[:8]}"
    script = Path(__file__).parents[2] / "scripts" / "init_tpch_data.py"
    conn = ClickHouseConnector(tls_config)
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--database",
                database,
                "--drop",
                "--protocol",
                "https",
                "--port",
                str(tls_config.port),
            ],
            env=dict(os.environ, CLICKHOUSE_PROTOCOL="invalid", CLICKHOUSE_PORT="not-a-port"),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "(https)" in result.stdout
        rows = conn.execute(
            {"sql_query": f"SELECT count() AS value FROM `{database}`.tpch_nation"}, result_format="list"
        )
        assert rows.success, rows.error
        assert rows.sql_return == [{"value": 25}]
    finally:
        try:
            result = conn.execute_ddl(f"DROP DATABASE IF EXISTS `{database}`")
            assert result.success, result.error
        finally:
            conn.close()
