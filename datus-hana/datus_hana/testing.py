"""Shared environment configuration for integration fixtures and readiness."""

import os

from .config import HanaConfig


def config_from_env() -> HanaConfig:
    return HanaConfig(
        host=os.getenv("HANA_HOST") or None,
        port=int(os.getenv("HANA_PORT", "23941")),
        username=os.getenv("HANA_USER") or None,
        password=os.getenv("HANA_PASSWORD", ""),
        userkey=os.getenv("HANA_USERKEY") or None,
        database=os.getenv("HANA_DATABASE") or None,
        schema=os.getenv("HANA_SCHEMA") or None,
        encrypt=os.getenv("HANA_ENCRYPT", "true").lower() == "true",
        ssl_trust_store=os.getenv("HANA_SSL_TRUST_STORE") or None,
        ssl_host_name_in_certificate=os.getenv("HANA_SSL_HOST_NAME_IN_CERTIFICATE") or None,
        timeout_seconds=5,
    )
