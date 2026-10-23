"""Disposable certificates must satisfy strict chain and hostname verification."""

import subprocess

import pytest

from ci.integration.generate_tls import generate


@pytest.mark.parametrize("fault", [None, "hostname", "ca"])
def test_generated_certificates_support_strict_server_verification(tmp_path, fault):
    generate(tmp_path)
    result = subprocess.run(
        [
            "openssl",
            "verify",
            "-x509_strict",
            "-purpose",
            "sslserver",
            "-verify_hostname",
            "127.0.0.1" if fault == "hostname" else "localhost",
            "-CAfile",
            str(tmp_path / ("wrong-ca.crt" if fault == "ca" else "ca.crt")),
            str(tmp_path / "server.crt"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if fault is None:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0
        assert (
            "hostname mismatch" if fault == "hostname" else "unable to get local issuer certificate"
        ) in result.stderr
