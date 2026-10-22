"""Connector-local deadlines for PostgreSQL wire drivers, including libpq.

libpq has no socket read-timeout option. Interrupt its socket at the checkout
deadline instead of leaving a worker blocked or installing a process-wide wait
callback. Duplicating the socket prevents descriptor reuse from affecting a
different connection when a deadline races with cleanup.
"""

import socket
import threading
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine, ExceptionContext
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import ConnectionPoolEntry


class SocketDeadline:
    """Interrupt one native database socket after a cancellable lease deadline.

    Args:
        connection: DBAPI connection exposing fileno() or pg8000's _usock.
        seconds: Positive lease budget; receipt of data does not reset it.
    """

    def __init__(self, connection: Any, seconds: float) -> None:
        fileno = getattr(connection, "fileno", None)
        descriptor = fileno() if fileno else connection._usock.fileno()
        borrowed = socket.socket(fileno=descriptor)
        try:
            self.socket = borrowed.dup()
        finally:
            borrowed.detach()
        self.expired = False
        self._active = True
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        with self._lock:
            if self._active:
                self.expired = True
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        """Cancel the timer and release the duplicated descriptor."""
        with self._lock:
            self._active = False
            self._timer.cancel()
            self.socket.close()


def configure_timeouts(engine: Engine, seconds: float, query_seconds: int) -> None:
    """Install connection-local initialization, checkout and pre-ping deadlines.

    Args:
        engine: Engine whose physical connections should receive these bounds.
        seconds: Socket lease budget, including fetches and pool reset.
        query_seconds: Server statement timeout, in seconds.
    """
    key = "datus_socket_deadline"

    def stop(connection: Any, record: ConnectionPoolEntry, *args: Any) -> SocketDeadline | None:
        deadline = record.info.pop(key, None)
        if deadline is not None:
            deadline.close()
        return deadline

    def arm(connection: Any, record: ConnectionPoolEntry, *args: Any) -> None:
        stop(connection, record)
        record.info[key] = SocketDeadline(connection, seconds)

    # insert=True also bounds the dialect's first-connection introspection.
    event.listen(engine, "connect", arm, insert=True)
    event.listen(engine, "checkout", arm)
    event.listen(engine, "close", stop)
    event.listen(engine, "invalidate", stop)

    @event.listens_for(engine, "checkin")
    def checked_in(connection: Any, record: ConnectionPoolEntry) -> None:
        deadline = stop(connection, record)
        if deadline is not None and deadline.expired and connection is not None:
            record.invalidate()

    ping = engine.dialect.do_ping

    def bounded_ping(connection: Any) -> bool:
        deadline = SocketDeadline(connection, seconds)
        try:
            return ping(connection)
        finally:
            deadline.close()

    engine.dialect.do_ping = bounded_ping

    @event.listens_for(engine, "handle_error", retval=True)
    def classify_deadline(context: ExceptionContext) -> OperationalError | None:
        if context.connection is not None:
            deadline = context.connection.info.get(key)
            if deadline is not None and deadline.expired:
                context.is_disconnect = True
                return OperationalError(
                    context.statement,
                    context.parameters,
                    TimeoutError("Database socket operation timed out"),
                    connection_invalidated=True,
                )

    @event.listens_for(engine, "connect")
    def statement_deadline(connection: Any, record: ConnectionPoolEntry) -> None:
        previous = connection.autocommit
        connection.autocommit = True
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(f"SET statement_timeout = {query_seconds * 1000}")
            finally:
                cursor.close()
        finally:
            connection.autocommit = previous
