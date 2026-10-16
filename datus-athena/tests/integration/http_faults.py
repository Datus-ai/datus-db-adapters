"""Loopback TLS proxy: forward signed Athena requests and inject transport faults.

Only the local SDK connection trusts the temporary certificate. Upstream HTTPS
uses normal certificate validation. Request credentials and headers are not logged.
"""

import http.client
import json
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
import pytest
from botocore.config import Config

from datus_athena import AthenaConnector

pytestmark = pytest.mark.integration


class AthenaFaultProxy:
    def __init__(self, directory, hostname):
        self.hostname = hostname
        self.scenario = "none"
        self.counts = {}
        self.starts = []
        self.injected = 0
        self.read_delay_seconds = 2.5
        self.lock = threading.Lock()
        self.certificate = directory / "proxy.pem"
        key = directory / "proxy.key"
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
                f"/CN={hostname}",
                "-addext",
                f"subjectAltName=DNS:{hostname}",
                "-keyout",
                str(key),
                "-out",
                str(self.certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.certificate, key)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                if self.path != f"{hostname}:443":
                    self.send_error(403)
                    return
                self.send_response(200)
                self.end_headers()
                self.connection = owner.context.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile("rb")
                self.wfile = self.connection.makefile("wb")
                self.close_connection = False

            def disconnect(self):
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            def reply(self, status, body, headers=()):
                self.send_response_only(status)
                for name, value in headers:
                    if name.lower() not in {"content-length", "transfer-encoding", "connection"}:
                        self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                operation = self.headers.get("X-Amz-Target", "").rsplit(".", 1)[-1]
                with owner.lock:
                    attempt = owner.counts[operation] = owner.counts.get(operation, 0) + 1
                    scenario = owner.scenario
                if operation == "GetQueryExecution" and scenario == "throttle" and attempt == 1:
                    owner.injected += 1
                    self.reply(
                        429,
                        json.dumps({"__type": "ThrottlingException", "Message": "Controlled proxy throttle"}).encode(),
                        [("Content-Type", "application/x-amz-json-1.1"), ("x-amzn-ErrorType", "ThrottlingException")],
                    )
                    return
                if operation == "GetQueryExecution" and scenario == "read_timeout":
                    owner.injected += 1
                    time.sleep(owner.read_delay_seconds)
                    self.disconnect()
                    return
                upstream = http.client.HTTPSConnection(hostname, timeout=15, context=ssl.create_default_context())
                try:
                    headers = {
                        name: value for name, value in self.headers.items() if name.lower() != "proxy-connection"
                    }
                    upstream.request("POST", self.path, body=body, headers=headers)
                    response = upstream.getresponse()
                    payload = response.read()
                    if operation == "StartQueryExecution" and response.status == 200:
                        with owner.lock:
                            owner.starts.append(
                                (json.loads(body)["ClientRequestToken"], json.loads(payload)["QueryExecutionId"])
                            )
                        if scenario == "lost_submit_response" and attempt == 1:
                            owner.injected += 1
                            self.disconnect()
                            return
                    self.reply(response.status, payload, response.getheaders())
                finally:
                    upstream.close()

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def handle_error(self, request, client_address):
                # Expected when the client times out or a fault closes a socket.
                pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def connector(self, config, **overrides):
        proxy_url = self.url

        class Session(boto3.Session):
            def client(self, service_name, **kwargs):
                kwargs["config"] = kwargs["config"].merge(Config(proxies={"https": proxy_url}))
                return super().client(service_name, **kwargs)

        values = config.model_copy(update={"ca_bundle": str(self.certificate), **overrides})
        return AthenaConnector(values, session=Session(region_name=config.region))
