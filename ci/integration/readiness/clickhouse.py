import os

from _common import require_connection

from datus_clickhouse import ClickHouseConfig, ClickHouseConnector

config = ClickHouseConfig(
    host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
    port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
    username=os.getenv("CLICKHOUSE_USER", "default_user"),
    password=os.getenv("CLICKHOUSE_PASSWORD", "default_test"),
    database=os.getenv("CLICKHOUSE_DATABASE", "default_test"),
    timeout_seconds=5,
)
require_connection("clickhouse", ClickHouseConnector(config))
