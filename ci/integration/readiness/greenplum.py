import os

from _common import require_connection

from datus_greenplum import GreenplumConfig, GreenplumConnector

config = GreenplumConfig(
    host=os.getenv("GREENPLUM_HOST", "127.0.0.1"),
    port=int(os.getenv("GREENPLUM_PORT", "5432")),
    username=os.getenv("GREENPLUM_USER", "gpadmin"),
    password=os.getenv("GREENPLUM_PASSWORD", "pivotal"),
    database=os.getenv("GREENPLUM_DATABASE", "test"),
    schema_name=os.getenv("GREENPLUM_SCHEMA", "public"),
    timeout_seconds=5,
)
require_connection("greenplum", GreenplumConnector(config))
