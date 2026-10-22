"""Database-free timeout wiring assertions shared by PostgreSQL adapters."""

import socket
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.engine import Engine


def assert_engine_timeout_configuration(
    engine: Engine, *, connect_seconds: int, read_seconds: int, query_seconds: int
) -> None:
    """Check real dialect arguments and installed connection lifecycle callbacks.

    Args:
        engine: Real engine created by the adapter under test.
        connect_seconds: Expected timeout at the final DBAPI connection boundary.
        read_seconds: Expected socket timer interval.
        query_seconds: Expected statement timeout sent through the DBAPI cursor.

    Raises:
        AssertionError: Dialect translation or timeout callback wiring is wrong.
    """
    with patch.object(engine.dialect.dbapi, "connect", return_value=Mock()) as connect:
        engine.pool._creator()
    parameters = connect.call_args.kwargs
    assert float(parameters.get("connect_timeout", parameters.get("timeout"))) == connect_seconds

    # Deliver only this package's callbacks: other connect listeners initialize
    # native UUID/type codecs and require a real vendor server/DBAPI object.
    # The callbacks themselves were installed by the actual connector, rather
    # than by calling configure_timeouts in the test.
    callbacks = [fn for fn in engine.pool.dispatch.connect if fn.__module__ == "datus_postgresql.timeouts"]
    assert len(callbacks) == 2, "Connector did not install socket and statement deadlines"
    for _ in range(2):
        local, peer = socket.socketpair()
        cursor = Mock()
        attributes = {"_usock": local} if engine.dialect.driver == "pg8000" else {"fileno": local.fileno}
        connection = SimpleNamespace(**attributes, autocommit=False, cursor=Mock(return_value=cursor))
        record = SimpleNamespace(info={})
        try:
            for callback in callbacks:
                callback(connection, record)
            deadline = record.info["datus_socket_deadline"]
            assert deadline._timer.interval == read_seconds
            cursor.execute.assert_called_once_with(f"SET statement_timeout = {query_seconds * 1000}")
            cursor.close.assert_called_once()
            assert connection.autocommit is False
        finally:
            engine.pool.dispatch.close(connection, record)
            local.close()
            peer.close()
        assert deadline.socket.fileno() == -1
        assert record.info == {}
