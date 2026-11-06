from contextlib import contextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy.exc import ProgrammingError

from datus_databricks import DatabricksConfig, DatabricksConnector


def _oauth_config(**overrides):
    values = {
        "host": "adb-1.19.azuredatabricks.net",
        "warehouse_id": "abc",
        "catalog": "main",
        "schema": "analytics",
        "profile": "dev",
    }
    values.update(overrides)
    return DatabricksConfig(**values)


@pytest.fixture
def connector():
    return DatabricksConnector(_oauth_config())


def test_connector_keeps_token_out_of_the_url(connector):
    assert connector.catalog_name == "main"
    assert connector.database_name == "analytics"
    assert connector.schema_name == ""
    assert connector.dialect == "databricks"
    assert "abc" in connector.connection_string
    assert "access_token" not in connector.connection_string
    assert connector.to_dict() == {
        "db_type": "databricks",
        "host": "adb-1.19.azuredatabricks.net",
        "http_path": "/sql/1.0/warehouses/abc",
        "catalog": "main",
        "schema": "analytics",
        "auth_mode": "oauth_profile",
        "profile": "dev",
    }


def test_oauth_profile_parameters_use_the_cli_cache(connector, monkeypatch):
    sdk_config = MagicMock()
    sdk_config.authenticate.return_value = {"Authorization": "Bearer refreshed"}
    monkeypatch.setattr(
        "datus_databricks.connector._profile_config",
        lambda profile, host: sdk_config if (profile, host) == ("dev", "adb-1.19.azuredatabricks.net") else None,
    )

    params = connector._connection_parameters()

    assert "access_token" not in params
    assert params["credentials_provider"]()() == {"Authorization": "Bearer refreshed"}


def test_access_token_parameters_are_not_stored_on_the_url():
    connector = DatabricksConnector(_oauth_config(auth_mode="access_token", profile=None, access_token="dapi-secret"))

    assert connector._connection_parameters() == {"access_token": "dapi-secret"}
    assert "dapi-secret" not in connector.connection_string
    assert "dapi-secret" not in repr(connector.databricks_config)
    assert connector.to_dict()["profile"] is None
    assert "access_token" not in connector.to_dict()


def test_names_and_quoting(connector):
    assert connector.quote_identifier("a`b") == "`a``b`"
    assert connector.full_name(table_name="orders") == "`main`.`analytics`.`orders`"
    assert connector.identifier(catalog_name="other", table_name="orders") == "other.analytics.orders"
    assert connector.get_schemas() == []
    assert connector.default_catalog() == "main"


def test_context_switch_is_skipped_for_the_connection_default(connector):
    conn = MagicMock()

    connector.do_switch_context(conn, catalog_name="main", database_name="analytics")

    conn.exec_driver_sql.assert_not_called()


def test_context_switch_runs_use_for_another_catalog(connector):
    conn = MagicMock()

    connector.do_switch_context(conn, catalog_name="other", database_name="analytics")

    statements = [call.args[0] for call in conn.exec_driver_sql.call_args_list]
    assert statements == ["USE CATALOG `other`", "USE SCHEMA `analytics`"]


def test_metadata_listing_uses_unity_catalog_statements(connector, monkeypatch):
    seen = []

    def fake_execute(sql, **kwargs):
        seen.append(sql)
        if sql.startswith("SHOW CATALOGS"):
            return pd.DataFrame({"catalog": ["main", "system"]})
        if sql.startswith("SHOW SCHEMAS"):
            return pd.DataFrame({"databaseName": ["analytics", "information_schema"]})
        if sql.startswith("SHOW TABLES"):
            return pd.DataFrame({"tableName": ["orders"]})
        return pd.DataFrame(
            {"col_name": ["id", "# Partition Information"], "data_type": ["bigint", ""], "comment": ["", ""]}
        )

    monkeypatch.setattr(connector, "_execute_pandas", fake_execute)

    assert connector.get_catalogs() == ["main", "system"]
    assert connector.get_databases() == ["analytics"]
    assert connector.get_tables() == ["main.analytics.orders"]
    assert connector.get_schema(table_name="orders") == [
        {
            "cid": 0,
            "name": "id",
            "type": "bigint",
            "comment": "",
            "nullable": True,
            "pk": False,
            "default_value": None,
        }
    ]
    assert seen[0] == "SHOW CATALOGS"
    assert seen[1] == "SHOW SCHEMAS IN `main`"
    assert seen[2] == "SHOW TABLES IN `main`.`analytics`"
    assert seen[3] == "SHOW VIEWS IN `main`.`analytics`"
    assert seen[4] == "DESCRIBE TABLE `main`.`analytics`.`orders`"


def test_schema_learning_returns_ddl_and_sample_rows(connector, monkeypatch):
    def fake_execute(sql, **kwargs):
        if sql.startswith("SHOW TABLES"):
            return pd.DataFrame({"tableName": ["orders", "customer_view"]})
        if sql.startswith("SHOW VIEWS"):
            return pd.DataFrame({"viewName": ["customer_view", "daily_mv"], "isMaterialized": [False, True]})
        if sql.startswith("SHOW CREATE TABLE"):
            return pd.DataFrame({"createtab_stmt": ["CREATE TABLE orders (id BIGINT)"]})
        if sql.startswith("SELECT * FROM"):
            return pd.DataFrame({"id": [1]})
        if sql.startswith("DESCRIBE"):
            return pd.DataFrame(
                {
                    "col_name": ["id", "# Partition Information", "event_date"],
                    "data_type": ["bigint", "", "date"],
                    "comment": ["", "", ""],
                }
            )
        raise AssertionError(sql)

    monkeypatch.setattr(connector, "_execute_pandas", fake_execute)

    assert connector.get_tables() == ["main.analytics.orders"]
    assert connector.get_views() == ["main.analytics.customer_view", "main.analytics.daily_mv"]
    assert connector.get_materialized_views() == ["main.analytics.daily_mv"]
    assert connector.get_schema(table_name="orders")[1]["name"] == "event_date"

    definitions = connector.get_tables_with_ddl(tables=["orders"])
    assert definitions[0]["table_name"] == "orders"
    assert definitions[0]["definition"] == "CREATE TABLE orders (id BIGINT)"
    assert definitions[0]["identifier"] == "main.analytics.orders"

    samples = connector.get_sample_rows(tables=["`main`.`analytics`.`orders`"], top_n=2)
    assert samples[0]["table_name"] == "orders"
    assert samples[0]["sample_rows"].splitlines()[0] == "id"
    assert "1" in samples[0]["sample_rows"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"database_name": "analytics"},
        {"catalog_name": "main"},
        {"catalog_name": "main", "database_name": "analytics"},
        {"schema_name": "analytics"},
    ],
)
def test_listed_names_are_always_three_part(connector, monkeypatch, kwargs):
    def fake_execute(sql, **kwargs):
        if sql.startswith("SHOW TABLES"):
            return pd.DataFrame({"tableName": ["orders"]})
        return pd.DataFrame({"viewName": ["daily_view"], "isMaterialized": [False]})

    monkeypatch.setattr(connector, "_execute_pandas", fake_execute)

    assert connector.get_tables(**kwargs) == ["main.analytics.orders"]
    assert connector.get_views(**kwargs) == ["main.analytics.daily_view"]


def test_missing_ddl_is_reported_without_failing_the_listing(connector, monkeypatch):
    def fail(sql, **kwargs):
        raise RuntimeError("permission denied")

    monkeypatch.setattr(connector, "_execute_pandas", fail)

    definitions = connector.get_tables_with_ddl(tables=["orders"])

    assert definitions[0]["definition"] == "-- DDL not available for orders"


def test_sample_rows_reject_a_non_positive_limit(connector):
    with pytest.raises(Exception, match="top_n must be a positive integer"):
        connector.get_sample_rows(tables=["orders"], top_n=0)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 AS n",
        "SHOW TABLES",
        "EXPLAIN SELECT 1",
    ],
)
def test_execute_runs_read_statements(connector, monkeypatch, sql):
    monkeypatch.setattr(connector, "_execute_query", lambda *args, **kwargs: [{"n": 1}])

    result = connector.execute({"sql_query": sql})

    assert result.success is True
    assert result.row_count == 1


def test_execute_runs_set_as_session_state(connector, monkeypatch):
    seen = {}

    def fake_content_set(sql):
        seen["sql"] = sql
        from datus_db_core import ExecuteSQLResult

        return ExecuteSQLResult(success=True, sql_query=sql, sql_return="Successful", row_count=0)

    monkeypatch.setattr(connector, "execute_content_set", fake_content_set)

    result = connector.execute({"sql_query": "SET timezone = UTC"})

    assert result.success is True
    assert seen["sql"] == "SET timezone = UTC"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t WHERE id = 1",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET a = s.a",
        "CREATE TABLE t (id BIGINT)",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN a STRING",
    ],
)
def test_execute_sends_write_and_structure_statements(connector, monkeypatch, sql):
    executed = []

    class _Connection:
        def exec_driver_sql(self, statement, execution_options=None):
            executed.append(statement)
            result = MagicMock()
            result.rowcount = 1
            result.inserted_primary_key = None
            result.lastrowid = None
            return result

        def commit(self):
            executed.append("COMMIT")

        def rollback(self):
            return None

    @contextmanager
    def fake_conn(*args, **kwargs):
        yield _Connection()

    monkeypatch.setattr(connector, "_conn", fake_conn)

    result = connector.execute({"sql_query": sql})

    assert result.success is True
    assert executed[0] == sql
    assert executed[-1] == "COMMIT"


def test_execute_query_reports_a_syntax_error(connector, monkeypatch):
    def fail(*args, **kwargs):
        raise ProgrammingError("SELECT", {}, Exception("syntax error near FROM"))

    monkeypatch.setattr(connector, "_execute_query", fail)

    result = connector.execute_query("SELECT FROM")

    assert result.success is False
    assert "syntax" in result.error.lower()


def test_close_swallows_driver_errors(connector):
    engine = MagicMock()
    engine.dispose.side_effect = RuntimeError("pool already closed")
    connector.engine = engine
    connector._owns_engine = True

    connector.close()

    assert connector.engine is None
