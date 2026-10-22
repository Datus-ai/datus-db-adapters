"""Exercise SQLAlchemy's real event dispatch at the PyHive DBAPI boundary."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from thrift.transport import TTransport

from datus_sqlalchemy.thrift import configure_thrift_timeouts


@pytest.fixture
def engine():
    engine = create_engine("hive://tester@db.test:10000/default")
    configure_thrift_timeouts(engine, seconds=7, query_seconds=11)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("auth", ["NOSASL", "NONE", "LDAP", "CUSTOM", "KERBEROS"])
def test_transport_retains_auth_and_uses_a_new_socket_for_each_connection(engine, auth):
    params = {"host": "db.test", "port": 10000, "username": "tester", "database": "default", "auth": auth}
    if auth in ("LDAP", "CUSTOM"):
        params["password"] = "test-password"
    if auth == "KERBEROS":
        params["kerberos_service_name"] = "hive"
    original = dict(params)
    with patch("pyhive.hive.get_installed_sasl") as sasl, patch.object(engine.dialect, "connect") as connect:
        for _ in range(2):
            engine.dialect.dispatch.do_connect(engine.dialect, None, [], params)
        transports = [call.kwargs["thrift_transport"] for call in connect.call_args_list]
        assert len(transports) == 2
        assert transports[0] is not transports[1]
        assert params == original
        for call, transport in zip(connect.call_args_list, transports):
            assert call.kwargs["username"] == "tester"
            assert call.kwargs["database"] == "default"
            assert "auth" not in call.kwargs
            assert "password" not in call.kwargs
            assert "host" not in call.kwargs
            if auth == "NOSASL":
                assert transport._TBufferedTransport__trans._timeout == 7
            else:
                assert transport._trans._timeout == 7
                transport.sasl_client_factory()
                assert sasl.call_args.kwargs == {
                    "host": "db.test",
                    "sasl_auth": "GSSAPI" if auth == "KERBEROS" else "PLAIN",
                    "service": "hive" if auth == "KERBEROS" else None,
                    "username": "tester",
                    "password": "x" if auth == "NONE" else params.get("password"),
                }


@pytest.mark.parametrize("params", [{"auth": "INVALID"}, {"auth": "LDAP"}, {"auth": "NONE", "password": "p"}])
def test_invalid_auth_does_not_call_driver(engine, params):
    with patch.object(engine.dialect, "connect") as connect:
        with pytest.raises(ValueError):
            engine.dialect.dispatch.do_connect(engine.dialect, None, [], params)
    connect.assert_not_called()


def test_connection_setup_failure_closes_thrift_transport(engine):
    with (
        patch("pyhive.hive.connect", side_effect=OSError("setup failed")),
        patch.object(TTransport.TBufferedTransport, "close", autospec=True) as close,
    ):
        with pytest.raises(OSError, match="setup failed"):
            engine.dialect.dispatch.do_connect(engine.dialect, None, [], {"auth": "NOSASL"})
    close.assert_called_once()


def test_each_new_connection_sends_query_deadline_on_every_statement(engine):
    for _ in range(2):
        execute = Mock(return_value="operation-handle")
        connection = SimpleNamespace(_client=SimpleNamespace(ExecuteStatement=execute))
        engine.pool.dispatch.connect(connection, None)
        for initial in (None, 99):
            request = SimpleNamespace(queryTimeout=initial)
            assert connection._client.ExecuteStatement(request) == "operation-handle"
            assert request.queryTimeout == 11
            execute.assert_called_with(request)


@pytest.mark.parametrize(
    "error, disconnected",
    [
        (OSError("read timeout"), True),
        (TTransport.TTransportException(message="socket closed"), True),
        (ValueError("bad SQL"), False),
    ],
)
def test_transport_errors_close_connection_but_server_errors_keep_it(engine, error, disconnected):
    transport = Mock()
    connection = SimpleNamespace(connection=SimpleNamespace(driver_connection=SimpleNamespace(_transport=transport)))
    context = SimpleNamespace(original_exception=error, connection=connection, is_disconnect=False)
    for handler in engine.dialect.dispatch.handle_error:
        handler(context)
    assert context.is_disconnect is disconnected
    assert transport.close.call_count == int(disconnected)


def test_setup_transport_error_without_connection_is_still_a_disconnect(engine):
    context = SimpleNamespace(original_exception=OSError("handshake timed out"), connection=None, is_disconnect=False)
    for handler in engine.dialect.dispatch.handle_error:
        handler(context)
    assert context.is_disconnect is True
