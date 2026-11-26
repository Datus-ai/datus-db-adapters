"""MongoDB integration settings.

| Environment variable | Default |
| --- | --- |
| MONGODB_HOST_PORT | 47017 |
| MONGODB_TEST_ADMIN_URI | Built from the local administrator settings below |
| MONGODB_TEST_USER | mongodb_test_admin |
| MONGODB_TEST_PASSWORD | mongodb_test_password |
"""

import os
from datetime import datetime, timezone
from urllib.parse import quote_plus, unquote_plus, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from bson.decimal128 import Decimal128
from bson.objectid import ObjectId
from pymongo import MongoClient

from datus_mongodb import MongoDBConfig, MongoDBConnector


def _reader_uri(admin_uri: str, database: str) -> str:
    parts = urlsplit(admin_uri)
    hosts = parts.netloc.rsplit("@", 1)[-1]
    # PyMongo accepts either query separator. Keep the options as supplied and
    # replace authSource case-insensitively.
    separator = ";" if ";" in parts.query else "&"
    options = [
        option
        for option in parts.query.split(separator)
        if option and unquote_plus(option.partition("=")[0]).lower() != "authsource"
    ]
    options.append(f"authSource={quote_plus(database)}")
    return urlunsplit((parts.scheme, f"reader:mongodb_test_read@{hosts}", f"/{database}", separator.join(options), ""))


@pytest.fixture(scope="module")
def order_database():
    port = os.environ.get("MONGODB_HOST_PORT", "47017")
    user = quote_plus(os.environ.get("MONGODB_TEST_USER", "mongodb_test_admin"))
    password = quote_plus(os.environ.get("MONGODB_TEST_PASSWORD", "mongodb_test_password"))
    uri = os.environ.get("MONGODB_TEST_ADMIN_URI", f"mongodb://{user}:{password}@127.0.0.1:{port}/?authSource=admin")
    name = f"datus_orders_{uuid4().hex}"
    with MongoClient(uri, serverSelectionTimeoutMS=5000) as admin:
        database = admin[name]
        database.command("createUser", "reader", pwd="mongodb_test_read", roles=[{"role": "read", "db": name}])
        try:
            database.orders.insert_many(
                [
                    {
                        "_id": ObjectId(f"{index:024x}"),
                        "order_no": index,
                        "status": "cancelled" if index == 5 else "paid",
                        "customer": {"city": "北京" if index == 4 else "上海"},
                        "ordered_at": datetime(2026, 11, index, tzinfo=timezone.utc),
                        "ratio": 10.10 if index == 1 else 0.0,
                        **({"amount": value} if index != 4 else {}),
                    }
                    for index, value in enumerate(
                        [Decimal128("10.10"), Decimal128("20.20"), None, None, Decimal128("99.99")], 1
                    )
                ]
            )
            database.command("create", "paid_orders", viewOn="orders", pipeline=[{"$match": {"status": "paid"}}])
            yield MongoDBConfig(uri=_reader_uri(uri, name), database=name)
        finally:
            database.command("dropUser", "reader")
            admin.drop_database(name)


@pytest.fixture
def connector(order_database):
    with MongoDBConnector(order_database) as connector:
        yield connector
