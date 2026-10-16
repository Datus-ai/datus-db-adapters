import os
from pathlib import Path

import pytest

from datus_mysql import MySQLConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def tls_config(config):
    if not os.getenv("DATUS_TEST_TLS_DIR") or not config.ssl_ca:
        pytest.skip("Disposable TLS overlay is not configured")
    return config.model_dump()


def test_verified_tls_and_new_pool_connections(tls_config):
    connector = MySQLConnector(tls_config)
    try:
        for _ in range(2):
            result = connector.execute_query("SHOW STATUS LIKE 'Ssl_cipher'", result_format="list")
            assert result.success, result.error
            assert result.sql_return[0]["Value"]
            connector.close()
    finally:
        connector.close()


@pytest.mark.parametrize("invalid", ["ca", "hostname"])
def test_incorrect_certificate_or_hostname_is_rejected(tls_config, invalid):
    if invalid == "ca":
        tls_config["ssl_ca"] = str(Path(os.environ["DATUS_TEST_TLS_DIR"]) / "wrong-ca.crt")
    else:
        # The disposable certificate is signed for localhost, with no IP SAN.
        tls_config["host"] = "127.0.0.1"
    connector = MySQLConnector(tls_config)
    try:
        result = connector.execute_query("SELECT 1", result_format="list")
        assert not result.success
        assert "certificate" in str(result.error).lower()
    finally:
        connector.close()
