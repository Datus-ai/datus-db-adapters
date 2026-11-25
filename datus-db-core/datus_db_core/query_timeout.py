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


def _deadline_error(seconds: int) -> DatusDbException:
    return DatusDbException(
        ErrorCode.DB_EXECUTION_TIMEOUT, message_args={"error_message": f"Query timed out after {seconds} seconds"}
    )


@contextmanager
def sqlite_query_timeout(connection: sqlite3.Connection, seconds: int) -> Iterator[None]:
    validate_query_timeout(seconds)
    deadline = monotonic() + seconds
    interrupted = False

    def progress() -> int:
        nonlocal interrupted
        interrupted = monotonic() >= deadline
        return int(interrupted)

    connection.set_progress_handler(progress, 1000)
    try:
        yield
    except sqlite3.OperationalError as exc:
        if interrupted and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT:
            raise _deadline_error(seconds) from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


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
