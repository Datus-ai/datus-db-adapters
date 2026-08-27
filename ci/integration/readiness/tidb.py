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
connector = TiDBConnector(config)

# A reachable TiDB is not the whole cluster: TiFlash registers itself with PD a
# moment later, and `ALTER TABLE ... SET TIFLASH REPLICA` is rejected until it
# does ("tiflash server count: 0"). Fail the probe so the caller keeps waiting.
try:
    result = connector.execute(
        {
            "sql_query": (
                "SELECT COUNT(*) AS store_count FROM information_schema.TIKV_STORE_STATUS WHERE LABEL LIKE '%tiflash%'"
            )
        },
        result_format="list",
    )
    if not result.success:
        raise RuntimeError(f"tidb store lookup failed: {result.error}")
    if int(result.sql_return[0]["store_count"]) < 1:
        raise RuntimeError("tidb has no registered TiFlash store yet")
except Exception:
    connector.close()
    raise

require_connection("tidb", connector)
