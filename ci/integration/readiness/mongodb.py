import os
from urllib.parse import quote_plus

from datus_mongodb import MongoDBConnector

from ._common import require_connection

port = os.environ.get("MONGODB_HOST_PORT", "47017")
user = quote_plus(os.environ.get("MONGODB_TEST_USER", "mongodb_test_admin"))
password = quote_plus(os.environ.get("MONGODB_TEST_PASSWORD", "mongodb_test_password"))
uri = os.environ.get("MONGODB_TEST_ADMIN_URI", f"mongodb://{user}:{password}@127.0.0.1:{port}/?authSource=admin")
require_connection("mongodb", MongoDBConnector({"uri": uri, "database": "admin", "timeout_seconds": 3}))
