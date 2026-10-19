"""Connection settings shared by local fixtures, readiness and setup commands."""

import os
from typing import Any, Mapping

from .config import ClickHouseConfig


def config_from_env(overrides: Mapping[str, Any] | None = None) -> ClickHouseConfig:
    """Apply explicit CLI/fixture overrides before validating environment values."""
    values = {
        "host": os.getenv("CLICKHOUSE_HOST", "localhost"),
        "port": os.getenv("CLICKHOUSE_PORT") or None,
        "username": os.getenv("CLICKHOUSE_USER", "default_user"),
        "password": os.getenv("CLICKHOUSE_PASSWORD", "default_test"),
        "database": os.getenv("CLICKHOUSE_DATABASE", "default_test"),
        "protocol": os.getenv("CLICKHOUSE_PROTOCOL", "http"),
        "verify": os.getenv("CLICKHOUSE_VERIFY", "true"),
        "ca_cert": os.getenv("CLICKHOUSE_CA_CERT") or None,
    }
    values.update(overrides or {})
    return ClickHouseConfig(**values)
