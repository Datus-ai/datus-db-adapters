"""Statement deadlines shared by packaged and built-in database connectors."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Timer
from time import monotonic
from typing import Any

from datus_db_core.exceptions import DatusDbException, ErrorCode


def validate_query_timeout(seconds: int) -> None:
    if type(seconds) is not int or seconds <= 0:
        raise ValueError("query_timeout_seconds must be a positive integer")


def validate_select_query_for_timeout(sql: str, dialect: str) -> None:
    from datus_db_core.constants import SQLType
    from datus_db_core.sql_utils import is_single_statement, parse_sql_type

    if not is_single_statement(sql) or parse_sql_type(sql, dialect) != SQLType.SELECT:
        raise ValueError("A per-query timeout can only be set on a single SELECT query")


def _deadline_error(seconds: int) -> DatusDbException:
    return DatusDbException(
        ErrorCode.DB_EXECUTION_TIMEOUT, message_args={"error_message": f"Query timed out after {seconds} seconds"}
    )


@contextmanager
def sqlite_query_timeout(connection: sqlite3.Connection, seconds: int) -> Iterator[None]:
    validate_query_timeout(seconds)
    previous_busy_ms = max(0, int(connection.execute("PRAGMA busy_timeout").fetchone()[0]))
    timeout_ms = seconds * 1000
    capped_busy = previous_busy_ms > timeout_ms
    if capped_busy:
        connection.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    deadline = monotonic() + seconds
    interrupted = False

    def progress() -> int:
        nonlocal interrupted
        interrupted = monotonic() >= deadline
        return int(interrupted)

    connection.set_progress_handler(progress, 1000)
    try:
        yield
        if monotonic() >= deadline:
            raise _deadline_error(seconds)
    except sqlite3.OperationalError as exc:
        code = getattr(exc, "sqlite_errorcode", None)
        base_code = (code & 0xFF) if isinstance(code, int) else None
        if interrupted and base_code == sqlite3.SQLITE_INTERRUPT:
            raise _deadline_error(seconds) from exc
        # Allow a small timer-jitter margin when SQLite's ms busy handler exits right at the cap.
        if base_code == sqlite3.SQLITE_BUSY and monotonic() >= deadline - 0.05:
            raise _deadline_error(seconds) from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)
        if capped_busy:
            connection.execute(f"PRAGMA busy_timeout = {previous_busy_ms}")


@contextmanager
def duckdb_query_timeout(cursor: Any, seconds: int, interrupt_error: type[Exception]) -> Iterator[None]:
    validate_query_timeout(seconds)
    expired = Event()

    def interrupt() -> None:
        expired.set()
        cursor.interrupt()

    timer = Timer(seconds, interrupt)
    timer.daemon = True
    timer.start()
    try:
        yield
    except interrupt_error as exc:
        if expired.is_set():
            raise _deadline_error(seconds) from exc
        raise
    finally:
        timer.cancel()
        # A timer already executing interrupt() must finish before the cursor closes.
        timer.join()
