import csv
import io
import json

import pytest
from bson.decimal128 import Decimal128

from datus_db_core import DatusDbException, ErrorCode
from datus_mongodb import MongoDBConnector

pytestmark = pytest.mark.integration


def test_connection_and_invalid_credentials(connector, order_database):
    assert connector.test_connection()
    wrong_uri = order_database.uri.get_secret_value().replace("mongodb_test_read", "wrong_password")
    with MongoDBConnector(order_database.model_copy(update={"uri": type(order_database.uri)(wrong_uri)})) as invalid:
        with pytest.raises(DatusDbException) as exc:
            invalid.test_connection()
    assert exc.value.code is ErrorCode.DB_AUTHENTICATION_FAILED
    assert "wrong_password" not in str(exc.value)


def test_collection_metadata_and_bson_document_queries(connector):
    assert connector.list_collections() == ["orders", "paid_orders"]
    metadata = connector.describe_collection("orders", sample_size=5)
    assert metadata["sample_fields"]["customer.city"] == ["str"]
    assert metadata["sample_fields"]["amount"] == ["Decimal128", "null"]

    rows = connector.find(
        "orders",
        {"customer.city": "上海", "status": "paid"},
        projection={"_id": 0, "order_no": 1, "amount": 1},
        sort=[("order_no", -1)],
        limit=2,
    )
    assert rows == [{"order_no": 3, "amount": None}, {"order_no": 2, "amount": Decimal128("20.20")}]
    assert "amount" not in connector.find("orders", {"order_no": 4}, limit=1)[0]
    assert connector.find(
        "orders",
        '{"_id": {"$oid": "000000000000000000000002"}}',
        projection={"_id": 0, "order_no": 1},
    ) == [{"order_no": 2}]


def test_bootstrap_metadata_definitions_and_samples(connector, order_database):
    tables = connector.get_tables_with_ddl(database_name=order_database.database)
    views = connector.get_views_with_ddl(database_name=order_database.database)
    assert [entry["table_name"] for entry in tables] == ["orders"]
    assert [entry["table_name"] for entry in views] == ["paid_orders"]
    assert tables[0]["database_name"] == order_database.database
    table_definition = json.loads(tables[0]["definition"])
    assert table_definition["type"] == "collection"
    assert table_definition["sample_fields"]["customer.city"] == ["str"]
    assert json.loads(views[0]["definition"])["type"] == "view"

    samples = connector.get_sample_rows(top_n=1, database_name=order_database.database, table_type="full")
    assert [entry["table_name"] for entry in samples] == ["orders", "paid_orders"]
    for entry in samples:
        sample = list(csv.DictReader(io.StringIO(entry["sample_rows"])))
        assert len(sample) == 1
        assert sample[0]["status"] == "paid"
        assert json.loads(sample[0]["customer"]) == {"city": "上海"}
    assert connector.get_sample_rows(top_n=0) == []


def test_native_aggregation_decimal_and_null_group(connector):
    results = connector.aggregate(
        "orders",
        [
            {"$match": {"status": "paid"}},
            {"$group": {"_id": "$customer.city", "order_count": {"$sum": 1}, "revenue": {"$sum": "$amount"}}},
            {"$sort": {"order_count": -1}},
        ],
        limit=2,
    )
    assert results == [
        {"_id": "上海", "order_count": 3, "revenue": Decimal128("30.30")},
        {"_id": "北京", "order_count": 1, "revenue": 0},
    ]

    limited = connector.aggregate("orders", [{"$sort": {"order_no": 1}}], limit=1)
    assert [row["order_no"] for row in limited] == [1]


@pytest.mark.parametrize("stage", ["$out", "$merge"])
def test_rejects_write_pipeline_on_a_live_connection(connector, stage):
    collections = connector.list_collections()
    rows = connector.find("orders", sort=[("order_no", 1)], limit=5)
    assert collections == ["orders", "paid_orders"]
    assert [row["order_no"] for row in rows] == [1, 2, 3, 4, 5]

    with pytest.raises(ValueError, match=r"^Unsupported MongoDB aggregation stage$"):
        connector.aggregate("orders", [{stage: "orders"}])

    assert connector.list_collections() == collections
    assert connector.find("orders", sort=[("order_no", 1)], limit=5) == rows


def test_sql_query_formats_and_empty_schema(order_database, tmp_path):
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(
        json.dumps(
            {
                order_database.database: {
                    "orders": {
                        "bsonType": "object",
                        "properties": {
                            "order_no": {"bsonType": "int"},
                            "status": {"bsonType": "string"},
                            "ratio": {"bsonType": "double"},
                            "amount": {"bsonType": ["decimal", "null"]},
                        },
                        "required": ["order_no", "status"],
                    }
                }
            }
        )
    )
    config = order_database.model_copy(update={"schema_file": schema_file})
    with MongoDBConnector(config) as connector:
        sql = "SELECT order_no, amount FROM orders WHERE order_no IN (2, 3) ORDER BY order_no"
        result = connector.execute({"sql_query": sql}, result_format="list")
        assert result.success, result.error
        assert result.sql_return == [{"order_no": 2, "amount": "20.20"}, {"order_no": 3, "amount": None}]
        csv = connector.execute_csv(sql)
        assert csv.success and csv.sql_return == "order_no,amount\n2,20.20\n3,\n"
        pandas = connector.execute_pandas(sql)
        assert pandas.success, pandas.error
        assert pandas.sql_return["order_no"].tolist() == [2, 3]
        assert pandas.sql_return["amount"].iloc[0] == "20.20"
        assert pandas.sql_return["amount"].isna().tolist() == [False, True]
        empty = connector.execute_arrow("SELECT order_no FROM orders WHERE order_no = 999")
        assert empty.success, empty.error
        assert empty.row_count == 0 and str(empty.sql_return.schema.field("order_no").type) == "int64"
        empty_count = connector.execute_arrow("SELECT COUNT(*) AS n FROM orders WHERE order_no = 999")
        assert empty_count.success, empty_count.error
        assert empty_count.sql_return.to_pylist() == [{"n": 0}]
        null_sum = connector.execute_query(
            "SELECT SUM(amount) AS total FROM orders WHERE order_no IN (3, 4)", result_format="list"
        )
        assert null_sum.success, null_sum.error
        assert null_sum.sql_return == [{"total": None}]
        distinct = connector.execute_arrow(
            "SELECT DISTINCT amount FROM orders WHERE status = 'paid' ORDER BY amount LIMIT 3"
        )
        assert distinct.success, distinct.error
        assert distinct.sql_return.to_pylist() == [{"amount": None}, {"amount": "10.10"}, {"amount": "20.20"}]

        predicates_and_orders = [
            ("amount = 10.10 AND ratio = 10.10", [1]),
            ("10.10 = amount", [1]),
            ("amount >= 10.10 AND amount <= 10.10", [1]),
            ("amount BETWEEN 10.10 AND 20.20", [1, 2]),
            ("amount IN (1.010E1, 20.20, -1.50)", [1, 2]),
            ("amount = 10.10000000000000000000000000000001", []),
        ]
        for predicate, order_numbers in predicates_and_orders:
            result = connector.execute_query(
                f"SELECT order_no FROM orders WHERE {predicate} ORDER BY order_no", result_format="list"
            )
            assert result.success, result.error
            assert result.sql_return == [{"order_no": number} for number in order_numbers], predicate
