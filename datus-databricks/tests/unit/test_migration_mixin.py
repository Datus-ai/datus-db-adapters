import pytest

from datus_databricks import DatabricksConnector
from datus_databricks.config import DatabricksConfig


@pytest.fixture
def connector():
    return DatabricksConnector(
        DatabricksConfig(
            host="adb-1.azuredatabricks.net",
            warehouse_id="abc",
            catalog="main",
            schema="analytics",
            profile="dev",
        )
    )


def test_capabilities_describe_databricks(connector):
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["supported"] is True
    assert capabilities["dialect_family"] == "databricks"
    assert "USING DELTA" in capabilities["example_ddl"]
    assert any("ENGINE" in item for item in capabilities["forbids"])


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("varchar(255)", "STRING"),
        ("BYTEA", "BINARY"),
        ("JSONB", "STRING"),
        ("HUGEINT", "DECIMAL(38,0)"),
        ("DECIMAL(10,2)", None),
    ],
)
def test_map_source_type(connector, source_type, expected):
    assert connector.map_source_type("mysql", source_type) == expected


def test_suggest_table_layout_has_no_forced_keys(connector):
    assert connector.suggest_table_layout([{"name": "id"}]) == {}


@pytest.mark.parametrize(
    ("ddl", "marker"),
    [
        ("CREATE TABLE t (id BIGINT AUTO_INCREMENT)", "AUTO_INCREMENT"),
        ("CREATE TABLE t (id INT) ENGINE=InnoDB", "ENGINE="),
        ("CREATE TABLE t (id INT) DISTRIBUTED BY HASH(id) BUCKETS 4", "BUCKETS"),
    ],
)
def test_validate_ddl_rejects_other_dialects(connector, ddl, marker):
    assert any(marker in error for error in connector.validate_ddl(ddl))


def test_validate_ddl_accepts_delta_table(connector):
    ddl = "CREATE TABLE t (id BIGINT, name STRING) USING DELTA"
    assert connector.validate_ddl(ddl) == []
