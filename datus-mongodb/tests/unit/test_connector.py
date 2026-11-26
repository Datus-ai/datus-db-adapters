from importlib.metadata import entry_points

import pytest
from pydantic import ValidationError

from datus_db_core import BaseSqlConnector, ConnectorRegistry
from datus_mongodb import MongoDBConfig, MongoDBConnector


def test_registry_discovers_sql_connector():
    entry_point = next(ep for ep in entry_points(group="datus.adapters") if ep.name == "mongodb")
    entry_point.load()()
    config = {"uri": "mongodb://127.0.0.1:27017", "database": "sales"}
    with ConnectorRegistry.create_connector("mongodb", config) as connector:
        assert isinstance(connector, BaseSqlConnector)
        assert connector.dialect == "mongodb"
        assert connector.get_databases() == ["sales"]
    assert ConnectorRegistry.get_metadata("mongodb").config_class is MongoDBConfig


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("DELETE FROM orders", "read-only"),
        ("SELECT order_no FROM orders; SELECT 2", "one read-only SELECT"),
        ("SELECT order_no FROM other.orders", "single collection in the configured database"),
        ("SELECT o.order_no FROM orders o JOIN other x ON o.order_no = x.order_no", "single collection"),
    ],
)
def test_sql_connector_rejects_writes_and_out_of_scope_queries(sql, error):
    with MongoDBConnector({"uri": "mongodb://127.0.0.1:1", "database": "sales"}) as connector:
        result = connector.execute({"sql_query": sql})
        assert not result.success
        assert error in result.error


@pytest.mark.parametrize("sql", ["SELECT 'unclosed FROM orders", "SELECT order_no FROM orders /*"])
def test_execute_query_reports_tokenization_errors(sql):
    with MongoDBConnector({"uri": "mongodb://127.0.0.1:1", "database": "sales"}) as connector:
        result = connector.execute_query(sql)
        assert not result.success
        assert result.sql_query == sql
        assert "Error tokenizing" in result.error


def test_config_redacts_uri_on_validation_failure():
    uri = "mysql://reader:secret-value@example.org"
    with pytest.raises(ValidationError, match="Expected a mongodb://") as exc:
        MongoDBConfig(uri=uri, database="sales")
    assert "secret-value" not in str(exc.value)


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$out": "export"}],
        [{"$merge": "export"}],
        [{"$lookup": {"from": "unrelated"}}],
        [{"$group": {"_id": None, "amount": {"$accumulator": {"init": "test"}}}}],
    ],
)
def test_rejects_unsupported_pipeline_before_using_driver(pipeline):
    with MongoDBConnector({"uri": "mongodb://127.0.0.1:1", "database": "sales"}) as connector:
        with pytest.raises(ValueError, match="Unsupported MongoDB aggregation stage|Server-side JavaScript"):
            connector.aggregate("orders", pipeline)


@pytest.mark.parametrize("limit", [True, 0, 10_001])
def test_find_rejects_invalid_limit(limit):
    with MongoDBConnector({"uri": "mongodb://127.0.0.1:1", "database": "sales"}) as connector:
        with pytest.raises(ValueError, match="Expected a row limit"):
            connector.find("orders", limit=limit)
