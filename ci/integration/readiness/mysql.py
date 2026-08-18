import os

from datus_mysql import MySQLConfig, MySQLConnector

from ._common import require_connection

config = MySQLConfig(
    host=os.getenv("MYSQL_HOST", "127.0.0.1"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    username=os.getenv("MYSQL_USER", "test_user"),
    password=os.getenv("MYSQL_PASSWORD", "test_password"),
    database=os.getenv("MYSQL_DATABASE", "test"),
    timeout_seconds=5,
)
require_connection("mysql", MySQLConnector(config))
