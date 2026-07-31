# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import exc as sa_exc

from datus_db_core import DatusDbException, ErrorCode
from datus_sqlalchemy import SQLAlchemyConnector


class DummySQLAlchemyConnector(SQLAlchemyConnector):
    def get_databases(self, catalog_name: str = "", include_sys: bool = False):
        return []

    def do_switch_context(self, conn, catalog_name="", database_name="", schema_name=""):
        if database_name:
            from sqlalchemy import text

            conn.execute(text(f"USE {database_name}"))
            conn.commit()


def test_conn_checks_out_and_returns_connection():
    """Each _conn() call checks out a connection from pool and returns it."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector._ensure_engine()
        with connector._conn() as conn:
            assert conn is mock_conn
        mock_conn.close.assert_called_once()


def test_conn_applies_context_per_checkout():
    """Each _conn() checkout calls do_switch_context with current thread's context."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    connector.switch_context(database_name="analytics")

    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector._ensure_engine()
        with connector._conn():
            pass

    # Verify USE analytics was executed on the checked-out connection
    mock_conn.execute.assert_called()
    sql_arg = str(mock_conn.execute.call_args_list[0][0][0].text)
    assert "analytics" in sql_arg
    mock_conn.commit.assert_called()


def test_execute_content_set_updates_thread_local_context():
    """USE db via execute_content_set updates the calling thread's context."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="mysql")
    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        result = connector.execute_content_set("USE analytics")

    assert result.success is True
    assert connector.database_name == "analytics"


def test_per_operation_connections_are_independent():
    """Two operations get independent connections from the pool."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    conn1, conn2 = MagicMock(), MagicMock()
    engine = MagicMock()
    engine.connect.side_effect = [conn1, conn2]

    query_result = MagicMock()
    query_result.fetchall.return_value = [MagicMock(_asdict=lambda: {"id": 1})]
    conn1.execute.return_value = MagicMock()  # for do_switch_context
    conn2.execute.return_value = query_result  # for the actual query

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector.execute_content_set("USE analytics")
        rows = connector._execute_query("SELECT id FROM users")

    assert engine.connect.call_count == 2
    assert rows == [{"id": 1}]


def test_thread_local_context_isolation_with_conn():
    """Two threads using the same connector get isolated contexts."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
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


# ==================== _conn() Context Override Tests ====================


def test_conn_explicit_params_override_thread_local():
    """Explicit _conn(database_name=...) overrides thread-local default."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    connector.switch_context(database_name="thread_default")

    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector._ensure_engine()
        with connector._conn(database_name="override_db"):
            pass

    sql_arg = str(mock_conn.execute.call_args_list[0][0][0].text)
    assert "override_db" in sql_arg
    assert "thread_default" not in sql_arg


def test_conn_falls_back_to_thread_local_when_no_override():
    """_conn() without explicit params uses thread-local context."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    connector.switch_context(database_name="my_default")

    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector._ensure_engine()
        with connector._conn():
            pass

    sql_arg = str(mock_conn.execute.call_args_list[0][0][0].text)
    assert "my_default" in sql_arg


# ==================== Execute Methods with Context Params ====================


def test_execute_query_with_context_params():
    """execute_query passes context to _conn()."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")

    mock_conn = MagicMock()
    query_result = MagicMock()
    query_result.fetchall.return_value = [MagicMock(_asdict=lambda: {"id": 1})]
    mock_conn.execute.return_value = query_result

    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        result = connector.execute_query("SELECT 1", database_name="target_db")

    assert result.success is True
    # do_switch_context should have been called with target_db
    sql_arg = str(mock_conn.execute.call_args_list[0][0][0].text)
    assert "target_db" in sql_arg


def test_execute_insert_with_context_params():
    """execute_insert passes context to _conn()."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")

    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_result.inserted_primary_key = None
    mock_conn.execute.return_value = mock_result

    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        result = connector.execute_insert("INSERT INTO t VALUES (1)", database_name="target_db")

    assert result.success is True
    sql_arg = str(mock_conn.execute.call_args_list[0][0][0].text)
    assert "target_db" in sql_arg


def test_execute_ddl_with_context_params():
    """execute_ddl passes context to _conn()."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")

    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_conn.execute.return_value = mock_result

    engine = MagicMock()
    engine.connect.return_value = mock_conn

    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        result = connector.execute_ddl("CREATE TABLE t (id INT)", catalog_name="cat", database_name="db")

    assert result.success is True


# ==================== Error Classification Tests ====================


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (
            sa_exc.OperationalError("SELECT 1", {}, Exception("connection refused by server")),
            ErrorCode.DB_CONNECTION_FAILED,
        ),
        (
            sa_exc.OperationalError("SELECT 1", {}, Exception("connection timed out")),
            ErrorCode.DB_CONNECTION_TIMEOUT,
        ),
        (
            sa_exc.OperationalError("SELECT 1", {}, Exception("authentication failed for user")),
            ErrorCode.DB_AUTHENTICATION_FAILED,
        ),
        (
            sa_exc.InterfaceError("SELECT 1", {}, Exception("permission denied for table")),
            ErrorCode.DB_PERMISSION_DENIED,
        ),
        (
            sa_exc.ProgrammingError("SELCT 1", {}, Exception("syntax error at or near SELCT")),
            ErrorCode.DB_EXECUTION_SYNTAX_ERROR,
        ),
        (
            sa_exc.IntegrityError("INSERT INTO t VALUES (1)", {}, Exception("duplicate key value")),
            ErrorCode.DB_CONSTRAINT_VIOLATION,
        ),
        (
            sa_exc.TimeoutError("QueuePool limit reached"),
            ErrorCode.DB_EXECUTION_TIMEOUT,
        ),
    ],
)
def test_handle_exception_classifies_and_logs_common_failures(exception, expected_code, caplog):
    """SQLAlchemy failures should map to stable Datus error categories."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")

    classified = connector._handle_exception(exception, "SELECT 1", "query")

    assert classified.code == expected_code
    assert "query failed; sql_preview='SELECT 1'; sql_chars=8" in caplog.text
    assert caplog.text.count("SELECT 1") == 1
    assert caplog.records[-1].exc_info[2] is exception.__traceback__


def test_ensure_engine_classifies_connection_creation_failure():
    """Connection setup failures should surface as stable Datus connection errors."""
    connector = DummySQLAlchemyConnector("postgresql://user:pass@127.0.0.1:1/test", dialect="postgresql")
    error = sa_exc.OperationalError("SELECT 1", {}, Exception("connection refused by server"))

    with patch("datus_sqlalchemy.connector.create_engine", side_effect=error):
        with pytest.raises(DatusDbException) as exc_info:
            connector._ensure_engine()

    assert exc_info.value.code == ErrorCode.DB_CONNECTION_FAILED


# ==================== _conn() Rollback on Exception ====================


def test_conn_rollback_on_exception():
    """_conn() calls conn.rollback() when an exception occurs."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")

    mock_conn = MagicMock()
    engine = MagicMock()
    engine.connect.return_value = mock_conn

    # Make do_switch_context raise to trigger the exception path
    with patch("datus_sqlalchemy.connector.create_engine", return_value=engine):
        connector._ensure_engine()
        with patch.object(connector, "do_switch_context", side_effect=RuntimeError("boom")):
            try:
                with connector._conn():
                    pass
            except RuntimeError:
                pass

    mock_conn.rollback.assert_called_once()
    mock_conn.close.assert_called_once()


# ==================== _ensure_engine Thread Safety ====================


def test_ensure_engine_concurrent_access():
    """Multiple threads calling _ensure_engine create only one engine."""
    connector = DummySQLAlchemyConnector("sqlite://", dialect="sqlite")
    call_count = 0

    def counting_create_engine(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        import time

        time.sleep(0.02)  # Simulate slow engine creation
        return MagicMock()

    results = {}

    def worker(thread_id):
        engine = connector._ensure_engine()
        results[thread_id] = engine

    with patch("datus_sqlalchemy.connector.create_engine", side_effect=counting_create_engine):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # Double-check locking should ensure create_engine is called exactly once
    assert call_count == 1
    # All threads should get the same engine instance
    engines = list(results.values())
    assert all(e is engines[0] for e in engines)
