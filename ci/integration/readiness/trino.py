import os

from _common import require_connection

from datus_trino import TrinoConfig, TrinoConnector

config = TrinoConfig(
    host=os.getenv("TRINO_HOST", "127.0.0.1"),
    port=int(os.getenv("TRINO_PORT", "8080")),
    username=os.getenv("TRINO_USER", "trino"),
    password=os.getenv("TRINO_PASSWORD", ""),
    catalog=os.getenv("TRINO_CATALOG", "tpch"),
    schema_name=os.getenv("TRINO_SCHEMA", "tiny"),
    http_scheme=os.getenv("TRINO_HTTP_SCHEME", "http"),
    timeout_seconds=5,
)
require_connection("trino", TrinoConnector(config))
