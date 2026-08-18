import os

from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector

from ._common import require_connection

config = PostgreSQLConfig(
    host=os.getenv("POSTGRESQL_HOST", "127.0.0.1"),
    port=int(os.getenv("POSTGRESQL_PORT", "5432")),
    username=os.getenv("POSTGRESQL_USER", "test_user"),
    password=os.getenv("POSTGRESQL_PASSWORD", "test_password"),
    database=os.getenv("POSTGRESQL_DATABASE", "test"),
    schema_name=os.getenv("POSTGRESQL_SCHEMA", "public"),
    timeout_seconds=5,
)
require_connection("postgresql", PostgreSQLConnector(config))
