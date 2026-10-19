from datus_clickhouse import ClickHouseConnector
from datus_clickhouse.testing import config_from_env

from ._common import require_connection

require_connection("clickhouse", ClickHouseConnector(config_from_env({"timeout_seconds": 5})))
