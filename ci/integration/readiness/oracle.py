import os

from datus_oracle import OracleConfig, OracleConnector

from ._common import require_connection

config = OracleConfig(
    host=os.getenv("ORACLE_HOST", "127.0.0.1"),
    port=int(os.getenv("ORACLE_PORT", "1521")),
    username=os.getenv("ORACLE_USER", "datus_test"),
    password=os.getenv("ORACLE_PASSWORD", "test_password"),
    service_name=os.getenv("ORACLE_SERVICE_NAME", "ORCLPDB1"),
    schema_name=os.getenv("ORACLE_SCHEMA", "DATUS_TEST"),
    timeout_seconds=5,
)
require_connection("oracle", OracleConnector(config))
