import pytest

from datus_sqlite import SQLiteConnector


def test_sqlite_semantics():
    db = SQLiteConnector.__new__(SQLiteConnector)
    assert db.describe_migration_capabilities()["dialect_family"] == "sqlite"
    assert db.map_source_type("postgres", "DECIMAL(12,2)") == "NUMERIC"
    assert db.validate_ddl("CREATE TABLE x (id INTEGER)") == []
    assert db.validate_ddl("CREATE TABLE x (id INTEGER) ENGINE=OLAP") == [
        "ENGINE clause is MySQL/ClickHouse syntax; SQLite does not support it"
    ]


@pytest.mark.parametrize(
    "ddl, expected",
    [
        (
            "CREATE TABLE x (id INT) DUPLICATE\n KEY(id)",
            "DUPLICATE KEY is StarRocks-only syntax; SQLite does not support it",
        ),
        (
            "CREATE TABLE x (id INT) DISTRIBUTED\nBY HASH(id) BUCKETS 4",
            "DISTRIBUTED BY ... BUCKETS is StarRocks syntax; SQLite does not support it",
        ),
        (
            "CREATE TABLE x (id INT) ENGINE   = OLAP",
            "ENGINE clause is MySQL/ClickHouse syntax; SQLite does not support it",
        ),
    ],
)
def test_foreign_layout_syntax_is_rejected_with_irregular_whitespace(ddl, expected):
    assert SQLiteConnector.__new__(SQLiteConnector).validate_ddl(ddl) == [expected]


@pytest.mark.parametrize(
    "source_type, expected",
    [
        ("VARCHAR(20)", "TEXT"),
        ("BOOLEAN", "INTEGER"),
        ("DOUBLE", "REAL"),
        ("TIMESTAMP", "TEXT"),
        ("INTEGER", None),
        ("TEXT", None),
    ],
)
def test_type_mapping_and_passthrough(source_type, expected):
    db = SQLiteConnector.__new__(SQLiteConnector)
    assert db.map_source_type("postgres", source_type) == expected


def test_layout_needs_no_engine_options():
    db = SQLiteConnector.__new__(SQLiteConnector)
    assert db.suggest_table_layout([]) == {}
    assert db.suggest_table_layout([{"name": "id", "type": "INTEGER"}]) == {}
