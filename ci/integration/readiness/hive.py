import os

from _common import require_connection

from datus_hive import HiveConfig, HiveConnector

config = HiveConfig(
    host=os.getenv("HIVE_HOST", "127.0.0.1"),
    port=int(os.getenv("HIVE_PORT", "10000")),
    username=os.getenv("HIVE_USERNAME", "hive"),
    password=os.getenv("HIVE_PASSWORD", ""),
    database=os.getenv("HIVE_DATABASE", "default"),
    auth=os.getenv("HIVE_AUTH") or None,
    timeout_seconds=5,
)
require_connection("hive", HiveConnector(config))
