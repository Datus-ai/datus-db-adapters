"""Connector-local deadlines for PostgreSQL wire drivers, including libpq.

libpq has no socket read-timeout option. Interrupt its socket at the checkout
deadline instead of leaving a worker blocked or installing a process-wide wait
callback. Duplicating the socket prevents descriptor reuse from affecting a
different connection when a deadline races with cleanup.
"""

import socket
import threading

from sqlalchemy import event
from sqlalchemy.exc import OperationalError


class SocketDeadline:
    def __init__(self, connection, seconds):
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

    def _expire(self):
        with self._lock:
            if self._active:
                self.expired = True
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self):
        with self._lock:
            self._active = False
            self._timer.cancel()
            self.socket.close()


def configure_timeouts(engine, seconds, query_seconds):
    """Bound initialization, pre-ping, checkout SQL, fetch, and pool reset."""
    key = "datus_socket_deadline"

    def stop(connection, record, *args):
        deadline = record.info.pop(key, None)
        if deadline is not None:
            deadline.close()
        return deadline

    def arm(connection, record, *args):
        stop(connection, record)
        record.info[key] = SocketDeadline(connection, seconds)

    # insert=True also bounds the dialect's first-connection introspection.
    event.listen(engine, "connect", arm, insert=True)
    event.listen(engine, "checkout", arm)
    event.listen(engine, "close", stop)
    event.listen(engine, "invalidate", stop)

    @event.listens_for(engine, "checkin")
    def checked_in(connection, record):
        deadline = stop(connection, record)
        if deadline is not None and deadline.expired and connection is not None:
            record.invalidate()

    ping = engine.dialect.do_ping

    def bounded_ping(connection):
        deadline = SocketDeadline(connection, seconds)
        try:
            return ping(connection)
        finally:
            deadline.close()

    engine.dialect.do_ping = bounded_ping

    @event.listens_for(engine, "handle_error", retval=True)
    def classify_deadline(context):
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
    def statement_deadline(connection, record):
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
