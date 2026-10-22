from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from datus_postgresql.timeouts import SocketDeadline, configure_timeouts


@pytest.mark.parametrize("field", ["timeout_seconds", "read_timeout_seconds", "query_timeout_seconds"])
@pytest.mark.parametrize("value", [0, -1])
def test_timeout_cannot_disable_bound(field, value):
    from pydantic import ValidationError

    from datus_postgresql import PostgreSQLConfig

    with pytest.raises(ValidationError):
        PostgreSQLConfig(**{"username": "test"}, **{field: value})


def test_socket_deadline_interrupts_read_and_releases_duplicate():
    import socket
    import time

    from datus_postgresql.timeouts import SocketDeadline

    local, peer = socket.socketpair()
    try:
        local.settimeout(1)
        started = time.monotonic()
        deadline = SocketDeadline(local, 0.05)
        try:
            assert local.recv(1) == b""
            assert deadline.expired
            assert time.monotonic() - started < 0.8
        finally:
            deadline.close()
        assert deadline.socket.fileno() == -1
    finally:
        local.close()
        peer.close()


@pytest.fixture
def lifecycle():
    """Real SQLAlchemy events and sockets; only the DBAPI calls are replaced."""
    import socket

    local, peer = socket.socketpair()
    cursor = Mock()
    connection = SimpleNamespace(
        fileno=local.fileno, autocommit=False, cursor=Mock(return_value=cursor), create_function=Mock()
    )
    record = SimpleNamespace(info={}, invalidate=Mock())
    engine = create_engine("sqlite://")
    # Initialize the real dialect before delivering the replacement DBAPI
    # connection through its lifecycle events.
    with engine.connect():
        pass
    configure_timeouts(engine, seconds=30, query_seconds=7)
    try:
        yield engine, connection, record, cursor, local, peer
    finally:
        engine.pool.dispatch.close(connection, record)
        engine.dispose()
        local.close()
        peer.close()


def test_connection_checkout_rearms_and_checkin_cancels_deadline(lifecycle):
    engine, connection, record, cursor, local, peer = lifecycle
    engine.pool.dispatch.connect(connection, record)
    first = record.info["datus_socket_deadline"]
    cursor.execute.assert_called_once_with("SET statement_timeout = 7000")
    cursor.close.assert_called_once()
    assert connection.autocommit is False

    engine.pool.dispatch.checkout(connection, record, None)
    second = record.info["datus_socket_deadline"]
    assert second is not first
    assert first.socket.fileno() == -1
    engine.pool.dispatch.checkin(connection, record)
    assert second.socket.fileno() == -1
    assert record.info == {}
    record.invalidate.assert_not_called()
    # A cancelled timer must not shut down the returned driver's socket.
    second._expire()
    peer.sendall(b"alive")
    local.settimeout(1)
    assert local.recv(5) == b"alive"


def test_expired_checkout_invalidates_on_checkin(lifecycle):
    engine, connection, record, _, _, _ = lifecycle
    engine.pool.dispatch.checkout(connection, record, None)
    deadline = record.info["datus_socket_deadline"]
    deadline._expire()
    engine.pool.dispatch.checkin(connection, record)
    record.invalidate.assert_called_once_with()
    assert record.info == {}
    assert deadline.socket.fileno() == -1


def test_socket_timeout_is_classified_as_disconnect_and_cleaned_up(lifecycle):
    engine, connection, record, _, _, peer = lifecycle
    engine.pool.dispatch.checkout(connection, record, None)
    deadline = record.info["datus_socket_deadline"]
    deadline._expire()
    context = SimpleNamespace(
        connection=SimpleNamespace(info=record.info), statement="SELECT stalled", parameters={}, is_disconnect=False
    )
    errors = [handler(context) for handler in engine.dialect.dispatch.handle_error]
    assert len(errors) == 1
    error = errors[0]
    assert isinstance(error, OperationalError)
    assert isinstance(error.orig, TimeoutError)
    assert error.connection_invalidated is True
    assert context.is_disconnect is True
    engine.pool.dispatch.invalidate(connection, record, error)
    assert deadline.socket.fileno() == -1
    assert record.info == {}
    peer.settimeout(1)
    assert peer.recv(1) == b""


@pytest.mark.parametrize("has_connection", [True, False])
def test_non_deadline_errors_are_not_reclassified(lifecycle, has_connection):
    engine, _, record, _, _, _ = lifecycle
    context = SimpleNamespace(
        connection=SimpleNamespace(info=record.info) if has_connection else None, is_disconnect=False
    )
    assert [handler(context) for handler in engine.dialect.dispatch.handle_error] == [None]
    assert context.is_disconnect is False


def test_failed_statement_setup_restores_autocommit_and_closes_cursor(lifecycle):
    engine, connection, record, cursor, _, _ = lifecycle
    cursor.execute.side_effect = RuntimeError("statement_timeout unsupported")
    with pytest.raises(RuntimeError, match="statement_timeout unsupported"):
        engine.pool.dispatch.connect(connection, record)
    assert connection.autocommit is False
    cursor.close.assert_called_once()


def test_pre_ping_has_its_own_deadline_and_releases_it_on_failure():
    import socket

    local, peer = socket.socketpair()
    engine = create_engine("sqlite://")
    deadlines = []

    def create_deadline(connection, seconds):
        deadline = SocketDeadline(connection, seconds)
        deadlines.append(deadline)
        return deadline

    def blocked_ping(connection):
        assert connection.recv(1) == b""
        raise OSError("pre-ping connection closed")

    try:
        local.settimeout(1)
        engine.dialect.do_ping = blocked_ping
        configure_timeouts(engine, seconds=0.05, query_seconds=7)
        with patch("datus_postgresql.timeouts.SocketDeadline", side_effect=create_deadline):
            with pytest.raises(OSError, match="pre-ping connection closed"):
                engine.dialect.do_ping(local)
        assert len(deadlines) == 1
        assert deadlines[0].expired is True
        assert deadlines[0].socket.fileno() == -1
    finally:
        engine.dispose()
        local.close()
        peer.close()


def test_pg8000_socket_fallback_is_bounded():
    import socket

    local, peer = socket.socketpair()
    try:
        local.settimeout(1)
        deadline = SocketDeadline(SimpleNamespace(_usock=local), 0.05)
        try:
            assert local.recv(1) == b""
            assert deadline.expired is True
        finally:
            deadline.close()
        assert deadline.socket.fileno() == -1
    finally:
        local.close()
        peer.close()
