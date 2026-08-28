import pytest

from datus_bigquery import BigQueryConnector


@pytest.fixture
def connector():
    return BigQueryConnector({"project": "my-project", "dataset": "analytics"})


def test_capabilities_are_bigquery_specific(connector):
    capabilities = connector.describe_migration_capabilities()

    assert capabilities["supported"] is True
    assert capabilities["dialect_family"] == "bigquery"
    assert "NOT ENFORCED" in " ".join(capabilities["requires"])
    assert "PARTITION BY" in capabilities["example_ddl"]
    assert "CLUSTER BY" in capabilities["example_ddl"]


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("BIGINT", "INT64"),
        ("varchar(255)", "STRING"),
        ("BYTEA", "BYTES"),
        ("JSONB", "JSON"),
        ("TIMESTAMP WITH TIME ZONE", "TIMESTAMP"),
        ("DECIMAL(10,2)", None),
    ],
)
def test_map_source_type(source_type, expected, connector):
    assert connector.map_source_type("source", source_type) == expected


@pytest.mark.parametrize(
    ("ddl", "marker"),
    [
        ("CREATE TABLE t (id BIGINT AUTO_INCREMENT)", "AUTO_INCREMENT"),
        ("CREATE TABLE t (id INT) ENGINE=InnoDB", "ENGINE"),
        ("CREATE TABLE t (id INT) DUPLICATE KEY(id)", "table models"),
        ("CREATE TABLE t (id INT) DISTRIBUTED BY HASH(id) BUCKETS 4", "DISTRIBUTED BY"),
        ("CREATE TABLE t (id INT64 PRIMARY KEY)", "NOT ENFORCED"),
    ],
)
def test_validate_ddl_rejects_non_bigquery_constructs(connector, ddl, marker):
    assert any(marker in error for error in connector.validate_ddl(ddl))


def test_validate_ddl_accepts_idiomatic_bigquery_ddl(connector):
    ddl = """
    CREATE TABLE `p.d.events` (
      id INT64 NOT NULL,
      event_ts TIMESTAMP,
      PRIMARY KEY (id) NOT ENFORCED
    )
    PARTITION BY DATE(event_ts)
    CLUSTER BY id
    """

    assert connector.validate_ddl(ddl) == []


def test_validate_ddl_ignores_comments_literals_and_longer_identifiers(connector):
    ddl = """
    -- AUTO_INCREMENT is intentionally not used
    CREATE TABLE `p.d.events` (
      serial_number STRING DEFAULT 'ENGINE=InnoDB',
      identity_label STRING
    )
    """

    assert connector.validate_ddl(ddl) == []


def test_layout_suggests_first_temporal_column(connector):
    assert connector.suggest_table_layout(
        [{"name": "id", "type": "INT64"}, {"name": "event_date", "type": "DATE"}]
    ) == {"partition_by": "event_date"}
