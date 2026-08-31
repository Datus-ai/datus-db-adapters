# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""TLS behaviour against a live DWS cluster.

The DWS default server certificate has CN=server and no subjectAltName, so
verify-full cannot succeed. That is asserted rather than skipped: if a future
cluster ships a proper certificate, this test tells us the docs need updating.
"""

import os

import pytest

from datus_db_core.exceptions import DatusDbException, ErrorCode
from datus_dws import DWSConfig, DWSConnector


def _connect_with(base_config: DWSConfig, **overrides) -> bool:
    connector = DWSConnector(base_config.model_copy(update=overrides))
    try:
        return connector.test_connection()
    finally:
        connector.close()


def _assert_rejected(base_config: DWSConfig, **overrides) -> None:
    """A rejected TLS handshake surfaces as DatusDbException, not a False return."""
    with pytest.raises(DatusDbException) as excinfo:
        _connect_with(base_config, **overrides)
    assert excinfo.value.code is ErrorCode.DB_CONNECTION_FAILED


@pytest.mark.integration
@pytest.mark.acceptance
def test_prefer_connects(base_config: DWSConfig):
    assert _connect_with(base_config, sslmode="prefer") is True


@pytest.mark.integration
def test_require_connects_with_encryption(base_config: DWSConfig):
    assert _connect_with(base_config, sslmode="require") is True


@pytest.mark.integration
@pytest.mark.acceptance
def test_verify_ca_succeeds_with_the_v2_ca(base_config: DWSConfig):
    if not base_config.sslrootcert:
        pytest.skip("DWS_SSLROOTCERT is required to verify the server certificate")

    assert _connect_with(base_config, sslmode="verify-ca") is True


@pytest.mark.integration
def test_verify_ca_fails_with_an_unrelated_ca(base_config: DWSConfig, tmp_path):
    """A CA that did not sign the server certificate must be rejected."""
    unrelated = tmp_path / "unrelated-ca.pem"
    unrelated.write_text(_UNRELATED_CA_PEM, encoding="utf-8")

    _assert_rejected(base_config, sslmode="verify-ca", sslrootcert=str(unrelated))


@pytest.mark.integration
def test_verify_full_cannot_succeed_against_the_default_certificate(base_config: DWSConfig):
    if not base_config.sslrootcert:
        pytest.skip("DWS_SSLROOTCERT is required to reach the hostname check")

    # CN=server with no SAN can never match a real endpoint. Documented in the
    # README; asserted here so the claim stays true.
    _assert_rejected(base_config, sslmode="verify-full")


@pytest.mark.integration
def test_inline_pem_certificate_is_accepted(base_config: DWSConfig):
    """A hosted caller uploads certificate content, not a path."""
    if not base_config.sslrootcert or not os.path.isfile(base_config.sslrootcert):
        pytest.skip("DWS_SSLROOTCERT must be a readable file for this test")

    with open(base_config.sslrootcert, encoding="utf-8") as handle:
        pem = handle.read()

    assert _connect_with(base_config, sslmode="verify-ca", sslrootcert=pem) is True


# An unrelated public root, used only to prove that verification is enforced.
_UNRELATED_CA_PEM = """-----BEGIN CERTIFICATE-----
MIIBtjCCAVugAwIBAgITBmyf1XSXNmY/Owua2eiedgPySjAKBggqhkjOPQQDAjA5
MQswCQYDVQQGEwJVUzEPMA0GA1UEChMGQW1hem9uMRkwFwYDVQQDExBBbWF6b24g
Um9vdCBDQSAzMB4XDTE1MDUyNjAwMDAwMFoXDTQwMDUyNjAwMDAwMFowOTELMAkG
A1UEBhMCVVMxDzANBgNVBAoTBkFtYXpvbjEZMBcGA1UEAxMQQW1hem9uIFJvb3Qg
Q0EgMzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABCmXp8ZBf8ANm+gBG1bG8lKl
ui2yEujSLtf6ycXYqm0fc4E7O5hrOXwzpcVOho6AF2hiRVd9RFgdszflZwjrZt6j
QjBAMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgGGMB0GA1UdDgQWBBSr
ttvXBp43rDCGB5Fwx5zEGbF4wDAKBggqhkjOPQQDAgNJADBGAiEA4IWSoxe3jfkr
BqWTrBqYaGFy+uGh0PsceGCmQ5nFuMQCIQCcAu/xlJyzlvnrxir4tiz+OpAUFteM
YyRIHN8wfdVoOw==
-----END CERTIFICATE-----
"""
