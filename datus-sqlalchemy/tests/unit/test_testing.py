"""Check the fault injector itself, including its independent safety deadline."""

import queue
import socket
import socketserver
import threading
from unittest.mock import patch

import pytest

from datus_sqlalchemy.testing import BlackholeProxy


@pytest.fixture
def echo_peer():
    received = queue.Queue()

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(1)
            try:
                while data := self.request.recv(1024):
                    self.request.sendall(data)
                    received.put(data)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Echo) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        worker.start()
        try:
            yield server.server_address, received
        finally:
            server.shutdown()
            worker.join(1)
            assert not worker.is_alive()


def test_proxy_forwards_then_discards_responses_and_recovers(echo_peer):
    address, received = echo_peer
    with BlackholeProxy(*address) as proxy, socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
        client.sendall(b"healthy")
        assert client.recv(1024) == b"healthy"
        assert received.get(timeout=1) == b"healthy"

        proxy.drop_responses.set()
        client.sendall(b"discard")
        assert received.get(timeout=1) == b"discard"
        client.settimeout(0.2)
        with pytest.raises(TimeoutError):
            client.recv(1024)

        proxy.drop_responses.clear()
        client.settimeout(1)
        client.sendall(b"recovered")
        assert client.recv(1024) == b"recovered"
        assert received.get(timeout=1) == b"recovered"
    assert all(not worker.is_alive() for worker in proxy._workers)


def test_proxy_safety_deadline_unblocks_a_client_without_its_own_deadline(echo_peer):
    address, received = echo_peer
    with BlackholeProxy(*address, safety_timeout=0.3) as proxy:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
            client.sendall(b"ready")
            assert client.recv(1024) == b"ready"
            assert received.get(timeout=1) == b"ready"
            proxy.drop_responses.set()
            client.sendall(b"stalled")
            assert received.get(timeout=1) == b"stalled"
            # Two seconds is only the test safety guard. The proxy must close
            # this connection itself after discarding the server's response.
            assert client.recv(1024) == b""
    assert all(sock.fileno() == -1 for sock in proxy._sockets)


def test_proxy_closes_client_when_upstream_cannot_connect():
    with patch("datus_sqlalchemy.testing.socket.create_connection", side_effect=ConnectionRefusedError):
        with BlackholeProxy("127.0.0.1", 1) as proxy:
            with socket.socket() as client:
                client.settimeout(1)
                client.connect(("127.0.0.1", proxy.port))
                assert client.recv(1024) == b""
    assert all(not worker.is_alive() for worker in proxy._workers)


def test_proxy_counts_complete_markers_across_fragments_and_reconnections(echo_peer):
    address, _ = echo_peer
    marker = b"datus_unique_marker"
    with BlackholeProxy(*address) as proxy:
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
                for fragment in (marker[:8], marker[8:]):
                    client.sendall(fragment)
                    assert client.recv(1024) == fragment
        assert proxy.marker_count(marker) == 2
        assert proxy.wait_for_closed(marker)
        assert proxy.wait_for_closed(b"missing", timeout=0.01) is False


def test_proxy_identifies_encrypted_transport_without_counting_ciphertext_as_sql(echo_peer):
    address, _ = echo_peer
    hello = b"\x16\x03\x01\x00\x05\x01hello"
    with BlackholeProxy(*address) as proxy:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as client:
            client.sendall(hello)
            assert client.recv(1024) == hello
            assert proxy.uses_tls is True
            assert proxy.marker_count(b"sql-request") == 0
        assert proxy.wait_for_closed(None)


def test_proxy_marker_trigger_allows_implicit_transaction_setup(echo_peer):
    address, received = echo_peer
    with BlackholeProxy(*address, safety_timeout=0.3) as proxy:
        proxy.drop_after_marker = b"tagged_query"
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
            client.sendall(b"BEGIN")
            assert client.recv(1024) == b"BEGIN"
            assert received.get(timeout=1) == b"BEGIN"
            client.sendall(b"tagged_query")
            assert received.get(timeout=1) == b"tagged_query"
            assert client.recv(1024) == b""
        assert proxy.marker_count(b"tagged_query") == 1


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_shared_timeout_contract_checks_real_mysql_driver(operation):
    from datus_mysql import MySQLConfig, MySQLConnector
    from datus_mysql.testing import StallingMySQLServer
    from datus_sqlalchemy.testing import assert_stalled_response_timeout

    with StallingMySQLServer(lambda sql: False) as server:
        config = MySQLConfig(host="127.0.0.1", port=server.port, username="test")
        assert_stalled_response_timeout(config, MySQLConnector, operation)
        assert server.connections == 2


def test_shared_timeout_contract_rejects_retrying_failed_connection_setup():
    from datus_db_core import DatusDbException
    from datus_mysql import MySQLConfig, MySQLConnector
    from datus_mysql.testing import StallingMySQLServer
    from datus_sqlalchemy.testing import assert_stalled_response_timeout

    class ReplayingConnector(MySQLConnector):
        def _execute_query(self, *args, **kwargs):
            try:
                return super()._execute_query(*args, **kwargs)
            except DatusDbException:
                return super()._execute_query(*args, **kwargs)

    with StallingMySQLServer(lambda sql: False) as server:
        config = MySQLConfig(host="127.0.0.1", port=server.port, username="test")
        with pytest.raises(AssertionError, match="retried DBAPI connection setup"):
            assert_stalled_response_timeout(config, ReplayingConnector, "query")
