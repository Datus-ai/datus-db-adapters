"""Environment configuration shared by integration fixtures and readiness."""

import os

from .config import TrinoConfig


def config_from_env() -> TrinoConfig:
    return TrinoConfig(
        host=os.getenv("TRINO_HOST", "localhost"),
        port=os.getenv("TRINO_PORT", "8080"),
        username=os.getenv("TRINO_USER", "trino"),
        password=os.getenv("TRINO_PASSWORD", ""),
        catalog=os.getenv("TRINO_CATALOG", "memory"),
        schema_name=os.getenv("TRINO_SCHEMA", "default"),
        http_scheme=os.getenv("TRINO_HTTP_SCHEME", "http"),
        verify=os.getenv("TRINO_VERIFY", "true"),
        ca_cert=os.getenv("TRINO_CA_CERT") or None,
    )
