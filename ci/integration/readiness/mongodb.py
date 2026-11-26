import os

from datus_mongodb import MongoDBConnector

from ._common import require_connection

port = os.environ.get("MONGODB_HOST_PORT", "47017")
uri = os.environ.get(
    "MONGODB_TEST_ADMIN_URI",
    f"mongodb://mongodb_test_admin:mongodb_test_password@127.0.0.1:{port}/?authSource=admin",
)
require_connection("mongodb", MongoDBConnector({"uri": uri, "database": "admin", "timeout_seconds": 3}))
