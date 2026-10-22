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
