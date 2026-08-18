import os

from datus_spark import SparkConfig, SparkConnector

from ._common import require_connection

config = SparkConfig(
    host=os.getenv("SPARK_HOST", "127.0.0.1"),
    port=int(os.getenv("SPARK_PORT", "10000")),
    username=os.getenv("SPARK_USER", "spark"),
    password=os.getenv("SPARK_PASSWORD", ""),
    database=os.getenv("SPARK_DATABASE", "default"),
    auth_mechanism=os.getenv("SPARK_AUTH_MECHANISM", "NONE"),
    timeout_seconds=5,
)
require_connection("spark", SparkConnector(config))
