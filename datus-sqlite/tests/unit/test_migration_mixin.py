from datus_sqlite import SQLiteConnector


def test_sqlite_semantics():
    db = SQLiteConnector.__new__(SQLiteConnector)
    assert db.describe_migration_capabilities()["dialect_family"] == "sqlite"
    assert db.map_source_type("postgres", "DECIMAL(12,2)") == "NUMERIC"
    assert db.validate_ddl("CREATE TABLE x (id INTEGER)") == []
    assert db.validate_ddl("CREATE TABLE x (id INTEGER) ENGINE=OLAP") == [
        "ENGINE clause is MySQL/ClickHouse syntax; SQLite does not support it"
    ]
