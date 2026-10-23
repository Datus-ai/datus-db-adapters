"""Shared TCP fault injection and timeout assertions for real-driver tests."""

import re
import selectors
import socket
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Literal, Self

from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.engine import Engine

from datus_db_core import DatusDbException

from .connector import SQLAlchemyConnector


class BlackholeProxy(AbstractContextManager):
    """Forward TCP traffic, optionally discarding replies with a safety cutoff.

    Args:
        host: Real database host.
        port: Real database port.
        safety_timeout: Seconds of silence before the proxy closes a connection.
    """

    def __init__(self, host: str, port: int, safety_timeout: float = 6) -> None:
        self.target = (host, port)
        self.safety_timeout = safety_timeout
        self.drop_responses = threading.Event()
        self.drop_after_marker: bytes | None = None
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self._traffic = threading.Condition()
        self._requests: dict[int, bytearray] = {}
        self._closed: set[int] = set()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen()
        self._listener.settimeout(0.1)
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self._stop.set()
        self._thread.join(1)
        self._listener.close()
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in self._workers:
            worker.join(4)
        assert not any(worker.is_alive() for worker in self._workers), "Fault proxy leaked a worker"

    def marker_count(self, marker: bytes) -> int:
        """Count a request marker across complete client streams, not TCP chunks.

        Args:
            marker: Unique ASCII SQL comment sent through the real driver.

        Returns:
            Number of marker occurrences, including reconnects and replays.
        """
        with self._traffic:
            return sum(payload.count(marker) for payload in self._requests.values())

    @property
    def uses_tls(self) -> bool:
        """Whether a client sent a TLS ClientHello through this proxy."""
        with self._traffic:
            return any(re.search(rb"\x16\x03[\x00-\x04]..\x01", data) for data in self._requests.values())

    def wait_for_closed(self, marker: bytes | None, timeout: float = 1) -> bool:
        """Wait for every connection that sent the marker to close.

        Args:
            marker: Request marker, or None to check all connections with TLS.
            timeout: Independent upper bound on this closure assertion.

        Returns:
            Whether marked connections existed and all closed within the bound.
        """
        with self._traffic:

            def closed() -> bool:
                matching = {key for key, data in self._requests.items() if marker is None or marker in data}
                return bool(matching) and matching.issubset(self._closed)

            return self._traffic.wait_for(closed, timeout)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            with self._traffic:
                identity = len(self._requests)
                self._requests[identity] = bytearray()
            worker = threading.Thread(target=self._forward, args=(client, identity), daemon=True)
            self._workers.append(worker)
            worker.start()

    def _forward(self, client: socket.socket, identity: int) -> None:
        try:
            with client:
                self._sockets.append(client)
                try:
                    upstream = socket.create_connection(self.target, timeout=3)
                except OSError:
                    return
                with upstream, selectors.DefaultSelector() as selector:
                    self._sockets.append(upstream)
                    upstream.settimeout(1)
                    client.settimeout(1)
                    selector.register(client, selectors.EVENT_READ, upstream)
                    selector.register(upstream, selectors.EVENT_READ, client)
                    silent_since = None
                    while not self._stop.is_set():
                        if self.drop_responses.is_set():
                            silent_since = silent_since or time.monotonic()
                            if time.monotonic() - silent_since >= self.safety_timeout:
                                return
                        else:
                            silent_since = None
                        for key, _ in selector.select(0.1):
                            try:
                                data = key.fileobj.recv(65536)
                                if not data:
                                    return
                                if key.fileobj is client:
                                    with self._traffic:
                                        self._requests[identity].extend(data)
                                        marker = self.drop_after_marker
                                        if marker is not None and marker in self._requests[identity]:
                                            self.drop_responses.set()
                                elif self.drop_responses.is_set():
                                    continue
                                key.data.sendall(data)
                            except OSError:
                                return
        finally:
            with self._traffic:
                self._closed.add(identity)
                self._traffic.notify_all()


def assert_stalled_response_timeout(
    config: BaseModel,
    connector_factory: Callable[[dict[str, Any]], SQLAlchemyConnector],
    operation: Literal["query", "metadata", "test_connection"],
    *,
    query: str = "SELECT 42 AS answer",
    settings_overrides: Mapping[str, Any] | None = None,
    engine_getter: Callable[[SQLAlchemyConnector], Engine] | None = None,
    max_seconds: float = 5,
) -> None:
    """Assert a live driver's timeout, physical closure, no replay and recovery.

    Args:
        config: Adapter config pointing at an already provisioned real database.
        connector_factory: Adapter constructor taking a configuration dictionary.
        operation: Public surface whose server response should be withheld.
        query: Portable query returning the exact row ``{"answer": 42}``.
        settings_overrides: Adapter-specific timeout overrides.
        engine_getter: Resolve the engine used by this operation when customized.
        max_seconds: Upper bound below the proxy's independent safety cutoff.

    Raises:
        AssertionError: A required timeout, closure or recovery guarantee fails.
    """
    import pytest

    marker = f"datus_timeout_{uuid.uuid4().hex}".encode()
    with BlackholeProxy(config.host, config.port, safety_timeout=max_seconds + 3) as proxy:
        settings = config.model_dump()
        # The verified-TLS fixtures identify the loopback endpoint as localhost.
        # Using its IP here would fail hostname verification before fault injection.
        settings.update(host="localhost", port=proxy.port, timeout_seconds=1)
        settings.update(settings_overrides or {})
        connector = connector_factory(settings)
        try:
            assert connector.test_connection()
            engine = engine_getter(connector) if engine_getter else connector._ensure_engine()
            # Some adapters deliberately dispose their pool after a probe.
            # Warm the operation's actual engine before observing reconnects.
            assert connector.execute_query(query, result_format="list").sql_return == [{"answer": 42}]
            attempts: list[str] = []
            invalidated: list[Any] = []
            closed: list[Any] = []
            opened: list[Any] = []
            connection_attempts: list[bool] = []
            driver_requests: list[str] = []

            def observe_creation(target: Engine) -> None:
                creator = target.pool._creator

                def create(connection_record: Any = None) -> Any:
                    connection_attempts.append(True)
                    return creator(connection_record)

                # do_connect observers can be skipped when an earlier custom
                # Thrift/auth hook returns a connection. Observe the pool's
                # actual creation boundary instead, without replacing DBAPI I/O.
                target.pool._creator = create

            def discard_reply(
                conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
            ) -> tuple[str, Any]:
                if not statement.lstrip().upper().startswith(("SET ", "USE ", "ALTER SESSION")):
                    attempts.append(statement)
                    if proxy.uses_tls:
                        proxy.drop_responses.set()
                    else:
                        # libpq may send an implicit BEGIN and wait for its
                        # response before sending this statement. Stall only
                        # after the actual tagged request reaches the server.
                        proxy.drop_after_marker = marker
                    statement += f" /* {marker.decode()} */"
                return statement, parameters

            def driver_execution(cursor: Any, statement: str, *args: Any) -> None:
                if marker.decode() in statement:
                    driver_requests.append(statement)

            event.listen(engine, "invalidate", lambda connection, *args: invalidated.append(connection))
            event.listen(engine, "close", lambda connection, *args: closed.append(connection))
            event.listen(engine, "connect", lambda connection, *args: opened.append(connection))
            observe_creation(engine)
            event.listen(engine, "do_execute", driver_execution)
            event.listen(engine, "do_execute_no_params", driver_execution)
            event.listen(engine, "before_cursor_execute", discard_reply, retval=True)
            started = time.monotonic()
            with pytest.raises(DatusDbException):
                if operation == "query":
                    connector._execute_query(query)
                elif operation == "metadata":
                    connector.get_tables()
                elif operation == "test_connection":
                    connector.test_connection()
                else:
                    raise AssertionError(f"Unknown timeout operation: {operation}")
            assert time.monotonic() - started < max_seconds
            assert len(attempts) == 1, "Adapter replayed or fell back after the timeout"
            assert len(driver_requests) == 1, "DBAPI executed more than one statement"
            assert driver_requests[0].count(marker.decode()) == 1
            # Preserve TLS in the live tests. For encrypted protocols count
            # requests at the DBAPI boundary; HTTP/plaintext protocols also get
            # an on-wire count, including retries internal to the HTTP driver.
            if proxy.uses_tls:
                assert proxy.marker_count(marker) == 0
                transport_marker = None
            else:
                marker_count = proxy.marker_count(marker)
                assert marker_count == 1, f"Expected one tagged wire request, observed {marker_count}"
                transport_marker = marker
            assert len(invalidated) == 1, "Timed-out DBAPI connection was not invalidated"
            assert closed == invalidated, "Timed-out DBAPI connection was not closed"
            assert opened == [], "Operation silently obtained a replacement DBAPI connection"
            assert connection_attempts == [], "Operation retried DBAPI connection setup"
            assert proxy.wait_for_closed(transport_marker), "Timed-out physical transport stayed open"
            event.remove(engine, "before_cursor_execute", discard_reply)
            proxy.drop_responses.clear()
            recovery_engine = engine_getter(connector) if engine_getter else connector._ensure_engine()
            if recovery_engine is not engine:
                event.listen(recovery_engine, "connect", lambda connection, *args: opened.append(connection))
                observe_creation(recovery_engine)
            result = connector.execute_query(query, result_format="list")
            assert result.success is True
            assert result.sql_return == [{"answer": 42}]
            assert len(opened) == 1, "Recovery must use a fresh DBAPI connection"
            assert connection_attempts == [True]
            assert opened[0] is not invalidated[0]
        finally:
            connector.close()
