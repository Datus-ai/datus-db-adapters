# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live TLS verification against the Docker-backed openGauss server."""

import os
import sys

import pytest

from datus_db_core import DatusDbException
from datus_gaussdb import GaussDBConnector

pytestmark = pytest.mark.integration


def _tls_file(name: str) -> str:
    value = os.getenv(name)
    if not value:
        pytest.skip(f"{name} is required for the TLS integration contract")
    return value


def _supported_drivers() -> list[str]:
    drivers = ["pg8000", "psycopg2"]
    if sys.platform == "linux" or os.getenv("GAUSSDB_FORCE_INTEGRATION") == "1":
        drivers.insert(0, "gaussdb")
    return drivers


@pytest.mark.parametrize("driver", _supported_drivers())
def test_verify_ca_accepts_the_trusted_server(config, driver):
    connector = GaussDBConnector(
        config.model_copy(
            update={
                "driver": driver,
                "sslmode": "verify-ca",
                "sslrootcert": _tls_file("GAUSSDB_SSLROOTCERT"),
            }
        )
    )
    try:
        assert connector.test_connection() is True
    finally:
        connector.close()


@pytest.mark.parametrize("driver", _supported_drivers())
def test_verify_ca_rejects_an_untrusted_server(config, driver):
    connector = GaussDBConnector(
        config.model_copy(
            update={
                "driver": driver,
                "sslmode": "verify-ca",
                "sslrootcert": _tls_file("GAUSSDB_WRONG_SSLROOTCERT"),
            }
        )
    )
    try:
        with pytest.raises(DatusDbException):
            connector.test_connection()
    finally:
        connector.close()


@pytest.mark.parametrize("driver", _supported_drivers())
def test_verify_full_accepts_a_matching_hostname(config, driver):
    connector = GaussDBConnector(
        config.model_copy(
            update={
                "host": "localhost",
                "driver": driver,
                "sslmode": "verify-full",
                "sslrootcert": _tls_file("GAUSSDB_SSLROOTCERT"),
            }
        )
    )
    try:
        assert connector.test_connection() is True
    finally:
        connector.close()


@pytest.mark.parametrize("driver", _supported_drivers())
def test_verify_full_rejects_a_hostname_mismatch(config, driver):
    connector = GaussDBConnector(
        config.model_copy(
            update={
                "driver": driver,
                "sslmode": "verify-full",
                "sslrootcert": _tls_file("GAUSSDB_SSLROOTCERT"),
            }
        )
    )
    try:
        with pytest.raises(DatusDbException):
            connector.test_connection()
    finally:
        connector.close()


@pytest.mark.parametrize("driver", _supported_drivers())
@pytest.mark.parametrize("sslmode", ["prefer", "require"])
def test_non_verifying_modes_negotiate_tls_without_a_ca(config, driver, sslmode):
    connector = GaussDBConnector(config.model_copy(update={"driver": driver, "sslmode": sslmode, "sslrootcert": None}))
    try:
        assert connector.test_connection() is True
    finally:
        connector.close()


@pytest.mark.parametrize("driver", _supported_drivers())
def test_disable_fails_when_the_server_requires_tls(config, driver):
    connector = GaussDBConnector(
        config.model_copy(update={"driver": driver, "sslmode": "disable", "sslrootcert": None})
    )
    try:
        with pytest.raises(DatusDbException):
            connector.test_connection()
    finally:
        connector.close()
