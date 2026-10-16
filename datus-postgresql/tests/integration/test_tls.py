import os
from pathlib import Path

import pytest

from datus_postgresql import PostgreSQLConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def tls_config(config):
    if not os.getenv("DATUS_TEST_TLS_DIR") or not config.sslrootcert:
        pytest.skip("Disposable TLS overlay is not configured")
    return config.model_dump()


def test_verified_tls_for_multiple_databases_and_reconnection(tls_config):
    connector = PostgreSQLConnector(tls_config)
    try:
        for database in [tls_config["database"], "postgres", tls_config["database"]]:
            result = connector.execute_query(
                "SELECT current_database() AS db, ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()",
                database_name=database,
                result_format="list",
            )
            assert result.success, result.error
            assert result.sql_return == [{"db": database, "ssl": True}]
        connector.close()
        assert connector.execute_query("SELECT 1", result_format="list").success
    finally:
        connector.close()


@pytest.mark.parametrize("invalid", ["ca", "hostname"])
def test_incorrect_certificate_or_hostname_is_rejected(tls_config, invalid):
    if invalid == "ca":
        tls_config["sslrootcert"] = str(Path(os.environ["DATUS_TEST_TLS_DIR"]) / "wrong-ca.crt")
    else:
        tls_config["host"] = "127.0.0.1"
    connector = PostgreSQLConnector(tls_config)
    try:
        result = connector.execute_query("SELECT 1", result_format="list")
        assert not result.success
        assert "certificate" in str(result.error).lower()
    finally:
        connector.close()
