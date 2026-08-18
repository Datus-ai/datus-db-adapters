import os
import sys

from datus_gaussdb import GaussDBConfig, GaussDBConnector

from ._common import require_connection

config = GaussDBConfig(
    host=os.getenv("GAUSSDB_HOST", "127.0.0.1"),
    port=int(os.getenv("GAUSSDB_PORT", "5432")),
    username=os.getenv("GAUSSDB_USER", "datus"),
    password=os.getenv("GAUSSDB_PASSWORD", "Datus@123"),
    database=os.getenv("GAUSSDB_DATABASE", "postgres"),
    schema_name=os.getenv("GAUSSDB_SCHEMA", "public"),
    driver=os.getenv("GAUSSDB_DRIVER") or ("pg8000" if sys.platform == "darwin" else "gaussdb"),
    sslmode=os.getenv("GAUSSDB_SSLMODE", "prefer"),
    sslrootcert=os.getenv("GAUSSDB_SSLROOTCERT") or None,
    timeout_seconds=5,
)
require_connection("gaussdb", GaussDBConnector(config))
