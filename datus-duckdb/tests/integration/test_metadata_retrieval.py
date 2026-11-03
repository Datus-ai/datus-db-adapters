import pytest

from datus_duckdb import DuckdbConnector

pytestmark = pytest.mark.integration


def test_metadata_filters_bind_quoted_names_and_preserve_scope():
    db = DuckdbConnector({"uri": ":memory:"})
    schema = "sales'2026"
    try:
        assert db.execute_ddl(f'CREATE SCHEMA "{schema}"').success
        assert db.execute_ddl(f'CREATE TABLE "{schema}".orders (id INTEGER)').success
        assert db.execute_ddl(f'CREATE VIEW "{schema}".order_view AS SELECT * FROM "{schema}".orders').success
        assert db.get_tables(database_name="memory", schema_name=schema) == ["orders"]
        assert db.get_views(database_name="memory", schema_name=schema) == ["order_view"]
        assert schema in db.get_schemas(database_name="memory")
        assert [t["table_name"] for t in db.get_tables_with_ddl(database_name="memory", schema_name=schema)] == [
            "orders"
        ]
        assert [t["table_name"] for t in db.get_views_with_ddl(database_name="memory", schema_name=schema)] == [
            "order_view"
        ]
        assert db.get_tables() == [f"memory.{schema}.orders"]
        # Treat SQL-looking names as values; no statement may drop the sentinel.
        malicious = "memory'; DROP TABLE \"sales'2026\".orders; --"
        assert db.get_schemas(database_name=malicious) == []
        assert db.get_tables(database_name=malicious) == []
        assert db.get_views(database_name=malicious) == []
        assert db.get_tables_with_ddl(database_name=malicious) == []
        assert db.get_tables(database_name="memory", schema_name=schema) == ["orders"]
    finally:
        db.close()
