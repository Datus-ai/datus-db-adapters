import sqlite3
from contextlib import closing
from threading import Event
from unittest.mock import Mock

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_db_core.query_timeout import (
    duckdb_query_timeout,
    sqlite_query_timeout,
    validate_query_timeout,
    validate_select_query_for_timeout,
)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_query_timeout_requires_a_positive_integer(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        validate_query_timeout(invalid)


@pytest.mark.parametrize(
    "dialect, sql, valid",
    [
        ("postgresql", "SELECT 1", True),
        ("postgresql", "SELECT 1;", True),
        ("postgresql", "SELECT 'a;b' AS txt; -- trailing comment", True),
        ("postgresql", "SELECT $$a;b$$ AS txt;", True),
        ("postgresql", r"SELECT E'\';' AS txt;", True),
        ("postgresql", r"SELECT '\' AS slash, ';' AS semi", True),
        ("postgresql", r"SELECT '\'; SET LOCAL statement_timeout = 0; SELECT '\'", False),
        ("duckdb", r"SELECT '\' AS slash, ';' AS semi", True),
        ("duckdb", r"SELECT '\'; CREATE TABLE leaked(id INT); SELECT '\'", False),
        ("duckdb", r"WITH x AS (SELECT '\' AS slash, ';' AS semi) INSERT INTO t SELECT * FROM x", False),
        ("sqlite", r"SELECT '\' AS slash, ';' AS semi", True),
        ("sqlite", r"SELECT '\'; CREATE TABLE leaked(id INT); SELECT '\'", False),
        ("mysql", r"SELECT '\';' AS txt;", True),
        ("tidb", r"SELECT '\';' AS txt;", True),
        ("postgresql", "SELECT 1; SELECT 2", False),
        ("postgresql", "SELECT 1; SET LOCAL statement_timeout = 0; SELECT 2", False),
        ("postgresql", "SELECT 1; CREATE TABLE leaked(id INT); SELECT 42", False),
        ("postgresql", "SELECT 1;;", False),
        ("postgresql", "; SELECT 1", False),
        ("postgresql", "SHOW TABLES", False),
    ],
)
def test_validate_select_query_for_timeout_requires_single_select(dialect, sql, valid):
    if valid:
        validate_select_query_for_timeout(sql, dialect)
    else:
        with pytest.raises(ValueError, match="single SELECT"):
            validate_select_query_for_timeout(sql, dialect)


def test_sqlite_clears_deadline_after_interrupting_current_statement(monkeypatch):
    monkeypatch.setattr("datus_db_core.query_timeout.monotonic", Mock(side_effect=[0, 2]))
    query = "WITH RECURSIVE t(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM t WHERE n<1000) SELECT MAX(n) FROM t"

    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(DatusDbException) as captured, sqlite_query_timeout(connection, 1):
            connection.execute(query).fetchone()
        assert captured.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connection.execute(query).fetchone() == (1000,)


def test_sqlite_lock_wait_is_bounded_and_restores_busy_timeout(tmp_path):
    from time import monotonic

    db_path = tmp_path / "locked.sqlite"
    with closing(sqlite3.connect(db_path)) as setup:
        setup.execute("PRAGMA journal_mode = DELETE")
        setup.execute("CREATE TABLE items (n INTEGER)")
        setup.execute("INSERT INTO items VALUES (1)")
        setup.commit()

    with (
        closing(sqlite3.connect(db_path, isolation_level=None, timeout=5.0)) as locker,
        closing(sqlite3.connect(db_path, timeout=5.0)) as reader,
    ):
        assert reader.execute("PRAGMA busy_timeout").fetchone() == (5000,)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            started = monotonic()
            with pytest.raises(DatusDbException) as captured, sqlite_query_timeout(reader, 1):
                reader.execute("SELECT n FROM items").fetchone()
            elapsed = monotonic() - started
            assert captured.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
            assert elapsed < 3.0
            assert reader.execute("PRAGMA busy_timeout").fetchone() == (5000,)

            # A shorter existing busy_timeout stays intact and surfaces the lock error as-is.
            reader.execute("PRAGMA busy_timeout = 50")
            with pytest.raises(sqlite3.OperationalError, match="locked"), sqlite_query_timeout(reader, 2):
                reader.execute("SELECT n FROM items").fetchone()
            assert reader.execute("PRAGMA busy_timeout").fetchone() == (50,)
        finally:
            locker.execute("ROLLBACK")
        assert reader.execute("SELECT n FROM items").fetchone() == (1,)


def test_sqlite_rejects_short_statement_that_finishes_after_deadline(monkeypatch):
    monkeypatch.setattr("datus_db_core.query_timeout.monotonic", Mock(side_effect=[0, 2]))
    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(DatusDbException) as captured, sqlite_query_timeout(connection, 1):
            connection.execute("SELECT 1").fetchone()
        assert captured.value.code == ErrorCode.DB_EXECUTION_TIMEOUT


def test_duckdb_deadline_calls_the_current_cursor_and_classifies_its_interrupt():
    interrupted = Event()
    cursor = Mock()
    cursor.interrupt.side_effect = interrupted.set

    class Interrupted(Exception):
        pass

    with pytest.raises(DatusDbException) as captured, duckdb_query_timeout(cursor, 1, Interrupted):
        assert interrupted.wait(3)
        raise Interrupted("native interruption")

    cursor.interrupt.assert_called_once_with()
    assert captured.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
