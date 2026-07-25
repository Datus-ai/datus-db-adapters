# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for BaseSqlConnector and utility functions."""

from typing import Any, List, Literal
from unittest.mock import MagicMock, patch

import pytest

from datus_db_core.base import BaseSqlConnector, list_to_in_str, to_sql_literal
from datus_db_core.config import ConnectionConfig
from datus_db_core.constants import SQLType
from datus_db_core.models import ExecuteSQLInput, ExecuteSQLResult
from datus_db_core.registry import ConnectorRegistry


class ConcreteConnector(BaseSqlConnector):
    """Concrete implementation for testing abstract base class."""

    def __init__(self, config=None, dialect="snowflake"):
        if config is None:
            config = ConnectionConfig()
        super().__init__(config, dialect)

    def execute_insert(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_update(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_delete(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_query(
        self,
        sql: str,
        result_format: Literal["csv", "arrow", "pandas", "list"] = "csv",
        catalog_name: str = "",
        database_name: str = "",
        schema_name: str = "",
    ) -> ExecuteSQLResult:
        return ExecuteSQLResult(
            success=True,
            sql_query=sql,
            row_count=0,
            sql_return="",
            result_format=result_format,
        )

    def execute_pandas(self, sql: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(
            success=True,
            sql_query=sql,
            row_count=0,
            sql_return="",
            result_format="pandas",
        )

    def execute_ddl(
        self, sql: str, catalog_name: str = "", database_name: str = "", schema_name: str = ""
    ) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=0, sql_return="", result_format="csv")

    def execute_csv(self, sql: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=0, sql_return="", result_format="csv")

    def get_databases(self, catalog_name: str = "", include_sys: bool = False) -> List[str]:
        return ["db1", "db2"]

    def get_tables(self, catalog_name: str = "", database_name: str = "", schema_name: str = "") -> List[str]:
        return ["table1", "table2"]

    def test_connection(self):
        return True

    def execute_queries(self, queries: List[str]) -> List[Any]:
        return [self.execute_query(q) for q in queries]

    def execute_content_set(self, sql_query: str) -> ExecuteSQLResult:
        return ExecuteSQLResult(
            success=True,
            sql_query=sql_query,
            row_count=0,
            sql_return="",
            result_format="csv",
        )


class ContextAwareConnector(ConcreteConnector):
    """Connector whose execute methods accept context kwargs and record what was passed.

    last_context/last_context_keys are per-thread so concurrent tests can
    inspect each thread's result without races.
    """

    _CTX_KEYS = {"catalog_name", "database_name", "schema_name"}

    def __init__(self, config=None, dialect="snowflake"):
        import threading

        super().__init__(config, dialect)
        self._local = threading.local()

    @property
    def last_context(self):
        return getattr(self._local, "last_context", {})

    @property
    def last_context_keys(self):
        return getattr(self._local, "last_context_keys", set())

    def _record(self, **kwargs):
        self._local.last_context_keys = set(kwargs.keys()) & self._CTX_KEYS
        self._local.last_context = {k: kwargs.get(k, "") for k in self._CTX_KEYS}

    def execute_insert(self, sql: str, **kwargs):
        self._record(**kwargs)
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_update(self, sql: str, **kwargs):
        self._record(**kwargs)
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_delete(self, sql: str, **kwargs):
        self._record(**kwargs)
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=1, sql_return="", result_format="csv")

    def execute_query(self, sql: str, result_format: Literal["csv", "arrow", "pandas", "list"] = "csv", **kwargs):
        self._record(**kwargs)
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=0, sql_return="", result_format=result_format)

    def execute_ddl(self, sql: str, **kwargs):
        self._record(**kwargs)
        return ExecuteSQLResult(success=True, sql_query=sql, row_count=0, sql_return="", result_format="csv")


class TestBaseSqlConnectorInit:
    def test_init_defaults(self):
        connector = ConcreteConnector()
        assert connector.dialect == "snowflake"
        assert connector.timeout_seconds == 30
        assert connector.catalog_name == ""
        assert connector.database_name == ""
        assert connector.schema_name == ""

    def test_get_current_context_returns_sql_coordinates(self):
        connector = ConcreteConnector()
        connector.switch_context(catalog_name="cat", database_name="db", schema_name="sch")

        assert connector.get_current_context() == {
            "catalog_name": "cat",
            "database_name": "db",
            "schema_name": "sch",
        }

    def test_init_with_config(self):
        config = ConnectionConfig(timeout_seconds=60)
        connector = ConcreteConnector(config=config, dialect="mysql")
        assert connector.dialect == "mysql"
        assert connector.timeout_seconds == 60

    def test_effective_capabilities_default_to_registry(self):
        original_connectors = ConnectorRegistry._connectors.copy()
        original_metadata = ConnectorRegistry._metadata.copy()
        original_capabilities = ConnectorRegistry._capabilities.copy()
        try:
            ConnectorRegistry.register("customdb", ConcreteConnector, capabilities={"database", "schema"})
            connector = ConcreteConnector(dialect="customdb")
            assert connector.get_effective_capabilities() == {"database", "schema"}
        finally:
            ConnectorRegistry._connectors = original_connectors
            ConnectorRegistry._metadata = original_metadata
            ConnectorRegistry._capabilities = original_capabilities


class TestClose:
    def test_close_no_error(self):
        connector = ConcreteConnector()
        connector.close()  # Should not raise


class TestContextManager:
    def test_enter_calls_connect(self):
        connector = ConcreteConnector()
        with patch.object(connector, "connect") as mock_connect:
            result = connector.__enter__()
            mock_connect.assert_called_once()
            assert result is connector

    def test_exit_calls_close(self):
        connector = ConcreteConnector()
        with patch.object(connector, "close") as mock_close:
            connector.__exit__(None, None, None)
            mock_close.assert_called_once()

    def test_exit_with_exception_calls_close(self):
        connector = ConcreteConnector()
        with patch.object(connector, "close") as mock_close:
            connector.__exit__(ValueError, ValueError("test"), None)
            mock_close.assert_called_once()

    def test_exit_returns_false(self):
        connector = ConcreteConnector()
        result = connector.__exit__(None, None, None)
        assert result is False


class TestValidateInput:
    def test_validate_dict_valid(self):
        connector = ConcreteConnector()
        connector.validate_input({"sql_query": "SELECT 1"})  # Should not raise

    def test_validate_dict_missing_sql_query(self):
        connector = ConcreteConnector()
        with pytest.raises(ValueError, match="'sql_query' parameter is required"):
            connector.validate_input({"other": "value"})

    def test_validate_dict_non_string_sql_query(self):
        connector = ConcreteConnector()
        with pytest.raises(ValueError, match="'sql_query' must be a string"):
            connector.validate_input({"sql_query": 123})

    def test_validate_object_valid(self):
        connector = ConcreteConnector()
        inp = ExecuteSQLInput(sql_query="SELECT 1")
        connector.validate_input(inp)  # Should not raise

    def test_validate_object_missing_sql_query(self):
        connector = ConcreteConnector()
        obj = MagicMock(spec=[])
        with pytest.raises(ValueError, match="'sql_query' parameter is required"):
            connector.validate_input(obj)

    def test_validate_object_non_string_sql_query(self):
        connector = ConcreteConnector()
        obj = MagicMock()
        obj.sql_query = 123
        with pytest.raises(ValueError, match="'sql_query' must be a string"):
            connector.validate_input(obj)


class TestExecuteRouting:
    def test_select_routes_to_execute_query(self):
        connector = ConcreteConnector()
        result = connector.execute({"sql_query": "SELECT * FROM t"})
        assert result.success is True

    def test_insert_routes_to_execute_insert(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_insert", wraps=connector.execute_insert) as mock:
            result = connector.execute({"sql_query": "INSERT INTO t VALUES (1)"})
            mock.assert_called_once()
        assert result.success is True

    def test_update_routes_to_execute_update(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_update", wraps=connector.execute_update) as mock:
            result = connector.execute({"sql_query": "UPDATE t SET col=1"})
            mock.assert_called_once()
        assert result.success is True

    def test_delete_routes_to_execute_delete(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_delete", wraps=connector.execute_delete) as mock:
            result = connector.execute({"sql_query": "DELETE FROM t WHERE id=1"})
            mock.assert_called_once()
        assert result.success is True

    def test_ddl_routes_to_execute_ddl(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_ddl", wraps=connector.execute_ddl) as mock:
            result = connector.execute({"sql_query": "CREATE TABLE t (id INT)"})
            mock.assert_called_once()
        assert result.success is True

    def test_content_set_routes_to_execute_content_set(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_content_set", wraps=connector.execute_content_set) as mock:
            result = connector.execute({"sql_query": "USE my_db"})
            mock.assert_called_once()
        assert result.success is True

    def test_show_routes_to_execute_query(self):
        connector = ConcreteConnector()
        result = connector.execute({"sql_query": "SHOW TABLES"})
        assert result.success is True

    def test_explain_routes_to_execute_explain(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_explain", wraps=connector.execute_explain) as mock:
            result = connector.execute({"sql_query": "EXPLAIN SELECT 1"})
            mock.assert_called_once()
        assert result.success is True

    def test_dict_input_converted(self):
        connector = ConcreteConnector()
        result = connector.execute({"sql_query": "SELECT 1"})
        assert result.success is True

    def test_object_input(self):
        connector = ConcreteConnector()
        inp = ExecuteSQLInput(sql_query="SELECT 1")
        result = connector.execute(inp)
        assert result.success is True

    def test_result_format_passed(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_query") as mock:
            mock.return_value = ExecuteSQLResult(
                success=True,
                sql_query="SELECT 1",
                row_count=0,
                sql_return="",
                result_format="arrow",
            )
            connector.execute({"sql_query": "SELECT 1"}, result_format="arrow")
            mock.assert_called_once_with("SELECT 1", "arrow")


class TestExecuteErrorHandling:
    def test_exception_returns_failure(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_query", side_effect=RuntimeError("DB down")):
            result = connector.execute({"sql_query": "SELECT 1"})
        assert result.success is False
        assert "DB down" in result.error

    def test_unknown_sql_type_returns_failure(self):
        connector = ConcreteConnector()
        with patch("datus_db_core.base.parse_sql_type", return_value=SQLType.UNKNOWN):
            result = connector.execute({"sql_query": "???"})
        assert result.success is False
        assert "Unknown type" in result.error

    def test_adapter_returned_failure_preserved(self):
        """Verify execute() does not overwrite adapter-returned success=False."""
        connector = ConcreteConnector()
        failed_result = ExecuteSQLResult(
            success=False,
            error="Constraint violation",
            sql_query="INSERT INTO t VALUES (1)",
            row_count=0,
            sql_return="",
            result_format="csv",
        )
        with patch.object(connector, "execute_insert", return_value=failed_result):
            result = connector.execute({"sql_query": "INSERT INTO t VALUES (1)"})
        assert result.success is False
        assert "Constraint violation" in result.error

    def test_input_result_format_used_when_method_default(self):
        """Verify ExecuteSQLInput.result_format is honored when method arg is default."""
        connector = ConcreteConnector()
        with patch.object(connector, "execute_query") as mock:
            mock.return_value = ExecuteSQLResult(
                success=True,
                sql_query="SELECT 1",
                row_count=0,
                sql_return="",
                result_format="arrow",
            )
            inp = ExecuteSQLInput(sql_query="SELECT 1", result_format="arrow")
            connector.execute(inp)
            mock.assert_called_once_with("SELECT 1", "arrow")


class TestExecuteContextPassThrough:
    """Test that execute() forwards catalog/database/schema to sub-methods via _call_with_ctx."""

    def test_select_receives_context(self):
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "SELECT 1"}, catalog_name="cat", database_name="db", schema_name="sch")
        assert connector.last_context == {"catalog_name": "cat", "database_name": "db", "schema_name": "sch"}

    def test_insert_receives_context(self):
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "INSERT INTO t VALUES (1)"}, database_name="db")
        assert connector.last_context["database_name"] == "db"

    def test_update_receives_context(self):
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "UPDATE t SET col=1"}, catalog_name="cat")
        assert connector.last_context["catalog_name"] == "cat"

    def test_delete_receives_context(self):
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "DELETE FROM t WHERE id=1"}, schema_name="sch")
        assert connector.last_context["schema_name"] == "sch"

    def test_ddl_receives_context(self):
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "CREATE TABLE t (id INT)"}, database_name="db", schema_name="sch")
        assert connector.last_context["database_name"] == "db"
        assert connector.last_context["schema_name"] == "sch"

    def test_empty_context_not_forwarded(self):
        """When no context is passed, no context kwargs reach the sub-method."""
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "SELECT 1"})
        assert connector.last_context_keys == set()

    def test_partial_context_only_forwards_non_empty(self):
        """Only non-empty context values are forwarded as kwargs."""
        connector = ContextAwareConnector()
        connector.execute({"sql_query": "SELECT 1"}, database_name="db")
        assert connector.last_context_keys == {"database_name"}
        assert connector.last_context["database_name"] == "db"


class TestCallWithCtx:
    """Test _call_with_ctx: context forwarding and TypeError fallback."""

    def test_forwards_context_when_method_accepts_it(self):
        connector = ContextAwareConnector()
        ctx = {"catalog_name": "cat", "database_name": "db"}
        result = BaseSqlConnector._call_with_ctx(connector.execute_insert, "INSERT INTO t VALUES (1)", ctx)
        assert result.success is True
        assert connector.last_context["catalog_name"] == "cat"
        assert connector.last_context["database_name"] == "db"

    def test_raises_when_method_rejects_context(self):
        """Methods without context kwargs trigger a clear TypeError."""

        def no_ctx_method(sql: str) -> ExecuteSQLResult:
            return ExecuteSQLResult(success=True, sql_query=sql, row_count=0, sql_return="", result_format="csv")

        ctx = {"catalog_name": "cat"}
        with pytest.raises(TypeError, match="does not accept per-call context overrides"):
            BaseSqlConnector._call_with_ctx(no_ctx_method, "INSERT INTO t VALUES (1)", ctx)

    def test_empty_ctx_skips_forwarding(self):
        connector = ConcreteConnector()
        result = BaseSqlConnector._call_with_ctx(connector.execute_insert, "INSERT INTO t VALUES (1)", {})
        assert result.success is True

    def test_passes_extra_args(self):
        connector = ContextAwareConnector()
        ctx = {"database_name": "db"}
        result = BaseSqlConnector._call_with_ctx(connector.execute_query, "SELECT 1", ctx, "arrow")
        assert result.success is True
        assert result.result_format == "arrow"
        assert connector.last_context["database_name"] == "db"

    def test_internal_type_error_propagates(self):
        """A TypeError raised inside the method body must not be swallowed."""

        def buggy_insert(sql, catalog_name="", database_name="", schema_name=""):
            raise TypeError("unsupported operand type(s) for +: 'int' and 'str'")

        ctx = {"catalog_name": "cat"}
        with pytest.raises(TypeError, match="unsupported operand"):
            BaseSqlConnector._call_with_ctx(buggy_insert, "INSERT INTO t VALUES (1)", ctx)


class TestSwitchContext:
    def test_switch_context_updates_names(self):
        connector = ConcreteConnector()
        connector.switch_context(catalog_name="cat", database_name="db", schema_name="sch")
        assert connector.catalog_name == "cat"
        assert connector.database_name == "db"
        assert connector.schema_name == "sch"

    def test_switch_context_partial_update(self):
        connector = ConcreteConnector()
        connector.catalog_name = "old_cat"
        connector.database_name = "old_db"
        connector.switch_context(schema_name="new_sch")
        assert connector.catalog_name == "old_cat"
        assert connector.database_name == "old_db"
        assert connector.schema_name == "new_sch"


class TestGetType:
    def test_get_type_returns_dialect(self):
        connector = ConcreteConnector(dialect="mysql")
        assert connector.get_type() == "mysql"


class TestExecuteExplain:
    def test_explain_delegates_to_execute_query(self):
        connector = ConcreteConnector()
        with patch.object(connector, "execute_query") as mock:
            mock.return_value = ExecuteSQLResult(success=True, sql_query="EXPLAIN SELECT 1", row_count=0, sql_return="")
            connector.execute_explain("EXPLAIN SELECT 1", "csv")
            mock.assert_called_once_with("EXPLAIN SELECT 1", "csv")


class TestThreadLocalContext:
    def test_thread_local_isolation(self):
        """Two threads switching context see their own values."""
        import threading

        connector = ConcreteConnector()
        results = {}

        def worker(thread_id, db_name):
            connector.switch_context(database_name=db_name)
            import time

            time.sleep(0.05)
            results[thread_id] = connector.database_name

        t1 = threading.Thread(target=worker, args=(1, "db1"))
        t2 = threading.Thread(target=worker, args=(2, "db2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results[1] == "db1"
        assert results[2] == "db2"

    def test_default_context_for_new_threads(self):
        """New threads get the default context, not another thread's context."""
        import threading

        connector = ConcreteConnector()
        connector._default_database = "default_db"
        connector.switch_context(database_name="main_thread_db")

        results = {}

        def worker():
            results["db"] = connector.database_name

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert results["db"] == "default_db"
        assert connector.database_name == "main_thread_db"

    def test_concurrent_execute_with_different_contexts(self):
        """Two threads calling execute() with different contexts don't interfere."""
        import threading
        import time

        connector = ContextAwareConnector()
        results = {}

        def worker(thread_id, db_name):
            connector.execute({"sql_query": "SELECT 1"}, database_name=db_name)
            time.sleep(0.05)
            results[thread_id] = connector.last_context.copy()

        t1 = threading.Thread(target=worker, args=(1, "db1"))
        t2 = threading.Thread(target=worker, args=(2, "db2"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert results[1]["database_name"] == "db1"
        assert results[2]["database_name"] == "db2"


class TestListToInStr:
    def test_with_values(self):
        result = list_to_in_str("WHERE col IN", ["a", "b", "c"])
        assert result == "WHERE col IN ('a','b','c')"

    def test_empty_list(self):
        assert list_to_in_str("WHERE col IN", []) == ""

    def test_none_list(self):
        assert list_to_in_str("WHERE col IN", None) == ""

    def test_single_value(self):
        result = list_to_in_str("IN", ["only"])
        assert result == "IN ('only')"

    def test_values_with_quotes(self):
        result = list_to_in_str("IN", ["it's"])
        assert result == "IN ('it''s')"


class TestToSqlLiteral:
    def test_none_returns_null(self):
        assert to_sql_literal(None) == "NULL"

    def test_empty_string_no_quotes(self):
        assert to_sql_literal("") == ""

    def test_empty_string_with_quotes(self):
        assert to_sql_literal("", around_with_quotes=True) == "''"

    def test_normal_string_no_quotes(self):
        assert to_sql_literal("hello") == "hello"

    def test_normal_string_with_quotes(self):
        assert to_sql_literal("hello", around_with_quotes=True) == "'hello'"

    def test_string_with_single_quote(self):
        assert to_sql_literal("it's", around_with_quotes=True) == "'it''s'"

    def test_none_with_quotes(self):
        assert to_sql_literal(None, around_with_quotes=True) == "NULL"
