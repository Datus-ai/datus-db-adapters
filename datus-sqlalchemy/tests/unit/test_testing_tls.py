"""Exercise the shared fault contract with real HTTPS and hostname verification."""

import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from datus_sqlalchemy.testing import BlackholeProxy, assert_stalled_response_timeout


@pytest.fixture
def tls_clickhouse_peer(tmp_path, monkeypatch):
    """A protocol peer with a trusted DNS-only certificate, like the CI overlay."""
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    cert, key = tmp_path / "localhost.crt", tmp_path / "localhost.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            query = self.rfile.read(int(self.headers["Content-Length"])).decode().upper()
            if "VERSION()" in query:
                response = b"version()\nString\n24.8.14.39\n"
            elif "CURRENTDATABASE()" in query:
                response = b"currentDatabase()\nString\ntest\n"
            elif "42 AS ANSWER" in query:
                response = b"answer\nUInt8\n42\n"
            else:
                response = b"1\nUInt8\n1\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            try:
                self.wfile.write(response)
            except OSError:
                pass  # The fault contract deliberately closes timed-out sockets.

        def log_message(self, *args):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        worker.start()
        try:
            yield server.server_port, cert
        finally:
            server.shutdown()
            worker.join(1)
            assert not worker.is_alive()


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_shared_timeout_contract_keeps_verified_tls_through_proxy(tls_clickhouse_peer, operation):
    from datus_clickhouse import ClickHouseConfig, ClickHouseConnector

    port, cert = tls_clickhouse_peer
    config = ClickHouseConfig(
        host="localhost", port=port, username="test", database="test", protocol="https", ca_cert=str(cert)
    )

    def verified_connector(settings):
        assert settings["protocol"] == "https"
        assert settings["verify"] is True
        assert settings["ca_cert"] == str(cert)
        return ClickHouseConnector(settings)

    assert_stalled_response_timeout(config, verified_connector, operation)


def test_fault_proxy_does_not_bypass_certificate_hostname_verification(tls_clickhouse_peer):
    """The fixture must reject the IP that used to break TLS timeout coverage."""
    port, cert = tls_clickhouse_peer
    context = ssl.create_default_context(cafile=str(cert))
    with BlackholeProxy("127.0.0.1", port) as proxy:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=1) as connection:
            with pytest.raises(ssl.SSLCertVerificationError, match="IP address mismatch"):
                context.wrap_socket(connection, server_hostname="127.0.0.1")
