import pytest

pytestmark = pytest.mark.integration


def test_system_table_filter_preserves_sqlite_named_user_tables(connector):
    for table in ["orders", "sqlite3_data", "sqliteabc"]:
        assert connector.execute_ddl(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY AUTOINCREMENT)').success
    expected = {"orders", "sqlite3_data", "sqliteabc"}
    assert set(connector.get_tables()) == expected
    assert {t["table_name"] for t in connector.get_tables_with_ddl()} == expected


@pytest.mark.parametrize("table", ["orders", 'my"table', 'x" UNION SELECT 99 --'])
def test_quoted_table_names_work_in_all_metadata_paths(connector, table):
    ref = connector.quote_identifier(table)
    assert connector.execute_ddl(f"CREATE TABLE {ref} (id INTEGER NOT NULL DEFAULT 7)").success
    assert connector.execute_insert(f"INSERT INTO {ref} VALUES (11)").success
    assert connector.full_name(table_name=table) == ref
    assert connector.get_schema(table_name=table) == [
        {"cid": 0, "name": "id", "type": "INTEGER", "nullable": False, "default_value": "7", "pk": 0}
    ]
    expected = [
        {
            "catalog_name": "",
            "database_name": "native",
            "schema_name": "",
            "table_name": table,
            "sample_rows": "id\n11\n",
        }
    ]
    assert connector.get_sample_rows(tables=[table], top_n=1) == expected
    assert connector.get_sample_rows(top_n=1) == expected
    definitions = connector.get_tables_with_ddl(tables=[table])
    assert [t["table_name"] for t in definitions] == [table]
    assert "DEFAULT 7" in definitions[0]["definition"]


@pytest.mark.parametrize("top_n", [-1, True, "1; SELECT 2", 1.5])
def test_sample_limit_rejects_invalid_inputs(orders, top_n):
    with pytest.raises(ValueError, match="non-negative integer"):
        orders.get_sample_rows(tables=["orders"], top_n=top_n)
    assert orders.execute_query("SELECT COUNT(*) AS n FROM orders", result_format="list").sql_return == [{"n": 4}]


def test_metadata_and_sample_limits(orders):
    assert orders.get_tables() == ["orders"]
    assert orders.get_sample_rows(tables=["orders"], top_n=0) == []
    assert orders.execute_ddl("CREATE VIEW recent_orders AS SELECT id FROM orders WHERE id=4").success
    assert orders.get_views() == ["recent_orders"]
    assert [v["table_name"] for v in orders.get_views_with_ddl()] == ["recent_orders"]
    assert orders.get_schema(table_name="missing") == []
