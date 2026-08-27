import os

from datus_tidb import TiDBConfig, TiDBConnector

from ._common import require_connection

config = TiDBConfig(
    host=os.getenv("TIDB_HOST", "127.0.0.1"),
    port=int(os.getenv("TIDB_PORT", "4000")),
    username=os.getenv("TIDB_USER", "root"),
    password=os.getenv("TIDB_PASSWORD", ""),
    database=os.getenv("TIDB_DATABASE", "test"),
    timeout_seconds=5,
)
require_connection("tidb", TiDBConnector(config))
