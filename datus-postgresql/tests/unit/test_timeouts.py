import pytest


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
