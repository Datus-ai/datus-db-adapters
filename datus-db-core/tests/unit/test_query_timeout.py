import sqlite3
from contextlib import closing
from threading import Event
from unittest.mock import Mock

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_db_core.query_timeout import duckdb_query_timeout, sqlite_query_timeout, validate_query_timeout


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "1"])
def test_query_timeout_requires_a_positive_integer(invalid):
    with pytest.raises(ValueError, match="positive integer"):
        validate_query_timeout(invalid)


def test_sqlite_clears_deadline_after_interrupting_current_statement(monkeypatch):
    monkeypatch.setattr("datus_db_core.query_timeout.monotonic", Mock(side_effect=[0, 2]))
    query = "WITH RECURSIVE t(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM t WHERE n<1000) SELECT MAX(n) FROM t"

    with closing(sqlite3.connect(":memory:")) as connection:
        with pytest.raises(DatusDbException) as captured, sqlite_query_timeout(connection, 1):
            connection.execute(query).fetchone()
        assert captured.value.code == ErrorCode.DB_EXECUTION_TIMEOUT
        assert connection.execute(query).fetchone() == (1000,)


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
