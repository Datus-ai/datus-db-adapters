"""Wire authentication tests; TLS/user-store cases need the corresponding setup."""

import os

import pytest

from datus_hana import HanaConfig, HanaConnector

pytestmark = pytest.mark.integration


@pytest.fixture
def password_config(config):
    if config.userkey:
        pytest.skip("Password authentication credentials are not configured")
    return config


@pytest.fixture
def tls_config(config):
    if not config.encrypt:
        pytest.skip("A TLS-enabled HANA test endpoint is required")
    return config


@pytest.fixture
def userkey_config(config):
    key = os.getenv("HANA_TEST_USERKEY")
    if not key:
        pytest.skip("HANA_TEST_USERKEY for the dedicated test user is not configured")
    return HanaConfig(**(config.model_dump() | {"username": None, "password": "", "userkey": key}))


def test_incorrect_password_is_rejected(password_config):
    config = HanaConfig(**(password_config.model_dump() | {"password": "DeliberatelyIncorrect9!"}))
    result = HanaConnector(config).execute_query("SELECT 1 FROM DUMMY", "list")
    assert not result.success
    assert "DeliberatelyIncorrect9!" not in result.error


def test_userstore_key_can_authenticate(userkey_config):
    assert HanaConnector(userkey_config).test_connection()


def test_valid_tls_connection(tls_config):
    assert tls_config.ssl_validate_certificate
    assert HanaConnector(tls_config).test_connection()


def test_certificate_hostname_mismatch_is_rejected(tls_config):
    config = HanaConfig(
        **(tls_config.model_dump() | {"ssl_host_name_in_certificate": "hana-test-certificate-mismatch.invalid"})
    )
    result = HanaConnector(config).execute_query("SELECT 1 FROM DUMMY", "list")
    assert not result.success
    assert result.error


def test_missing_trust_store_is_rejected(tls_config, tmp_path):
    config = HanaConfig(**(tls_config.model_dump() | {"ssl_trust_store": str(tmp_path / "nonexistent-ca.pem")}))
    result = HanaConnector(config).execute_query("SELECT 1 FROM DUMMY", "list")
    assert not result.success
    assert result.error
