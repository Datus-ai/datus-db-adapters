# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for the pg8000-based GaussDB authentication path.

The handshake tests run a fake GaussDB server on a socketpair — no real
database, no network — and drive the full ``Connection.__init__`` against
it, proving the startup packet carries protocol 3.51 and the SHA256
response matches the RFC 5802 scheme.
"""

import hashlib
import inspect
import socket
import struct
import threading

import pytest
from pg8000.core import CoreConnection, i_pack

from datus_gaussdb._pg8000_gauss import PROTOCOL_3_51, Connection, InterfaceError, _GaussCore, rfc5802_response

# Vector cross-checked against py_opengauss's independent implementation of
# the same algorithm (openGauss's official pure-Python driver).
VECTOR_PASSWORD = b"Datus@123"
VECTOR_RANDOM64 = b"a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"
VECTOR_TOKEN = b"1a2b3c4d"
VECTOR_ITERATION = 10000
VECTOR_RESPONSE = b"9db5944272406c1a698e95e009064beb5a7a9de9a2c7a9faf5574a853056a2a3"


def test_rfc5802_known_answer():
    assert rfc5802_response(VECTOR_PASSWORD, VECTOR_RANDOM64, VECTOR_TOKEN, VECTOR_ITERATION) == VECTOR_RESPONSE


def test_rfc5802_iteration_changes_response():
    other = rfc5802_response(VECTOR_PASSWORD, VECTOR_RANDOM64, VECTOR_TOKEN, VECTOR_ITERATION + 1)
    assert other != VECTOR_RESPONSE


class FakeGaussServer(threading.Thread):
    """Minimal server side of the GaussDB handshake over a socketpair."""

    def __init__(self, sock, auth_payload):
        super().__init__(daemon=True)
        self.sock = sock
        self.auth_payload = auth_payload
        self.startup_protocol = None
        self.auth_response = None

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def run(self):
        try:
            (length,) = struct.unpack("!i", self._read(4))
            startup = self._read(length - 4)
            (self.startup_protocol,) = struct.unpack("!i", startup[:4])

            # AuthenticationRequest code 10 with the configured payload.
            body = struct.pack("!i", 10) + self.auth_payload
            self.sock.sendall(b"R" + i_pack(len(body) + 4) + body)

            # PasswordMessage: 'p' + length + response + NUL.
            tag = self._read(1)
            assert tag == b"p"
            (plen,) = struct.unpack("!i", self._read(4))
            self.auth_response = self._read(plen - 4).rstrip(b"\x00")

            # AuthenticationOk + ReadyForQuery.
            self.sock.sendall(b"R" + i_pack(8) + struct.pack("!i", 0))
            self.sock.sendall(b"Z" + i_pack(5) + b"I")
        except Exception:  # pragma: no cover - surfaces as client-side failure
            self.sock.close()


def _handshake(auth_payload, password="Datus@123"):
    client_sock, server_sock = socket.socketpair()
    server = FakeGaussServer(server_sock, auth_payload)
    server.start()
    try:
        conn = Connection("datus", password=password, sock=client_sock, ssl_context=False)
        conn.close()
    finally:
        server.join(timeout=5)
        server_sock.close()
    assert not server.is_alive()
    return server


def _sha256_payload():
    return struct.pack("!I", 2) + VECTOR_RANDOM64 + VECTOR_TOKEN + struct.pack("!I", VECTOR_ITERATION)


def test_startup_packet_carries_protocol_351():
    server = _handshake(_sha256_payload())
    assert server.startup_protocol == PROTOCOL_3_51


def test_sha256_handshake_sends_rfc5802_response():
    server = _handshake(_sha256_payload())
    assert server.auth_response == VECTOR_RESPONSE


def _assert_iteration_refused(iteration: int):
    """Drive the handshake against a server naming ``iteration`` and assert
    the client refuses before any key-derivation work; both socket ends and
    the server thread are cleaned up whichever way the assertion goes."""
    payload = struct.pack("!I", 2) + VECTOR_RANDOM64 + VECTOR_TOKEN + struct.pack("!I", iteration)
    client_sock, server_sock = socket.socketpair()
    server = FakeGaussServer(server_sock, payload)
    server.start()
    try:
        with pytest.raises(InterfaceError, match="PBKDF2 iterations"):
            Connection("datus", password="Datus@123", sock=client_sock, ssl_context=False)
    finally:
        client_sock.close()
        server_sock.close()
        server.join(timeout=5)
    assert not server.is_alive()


def test_excessive_iteration_count_is_refused():
    """A hostile server naming an absurd PBKDF2 iteration count must be
    rejected before any key derivation work happens."""
    _assert_iteration_refused(2**31)


def test_zero_iteration_count_is_refused():
    _assert_iteration_refused(0)


def test_md5_method_under_code_10():
    salt = b"\x01\x02\x03\x04"
    server = _handshake(struct.pack("!I", 1) + salt)
    inner = hashlib.md5(b"Datus@123" + b"datus").hexdigest().encode("ascii")
    expected = b"md5" + hashlib.md5(inner + salt).hexdigest().encode("ascii")
    assert server.auth_response == expected


def _core_stub():
    """A bare _GaussCore carrying just the state the auth handler touches."""
    stub = _GaussCore.__new__(_GaussCore)
    stub.user = b"datus"
    stub.password = b"pw"
    return stub


def test_sm3_method_raises_actionable_error():
    payload = struct.pack("!i", 10) + struct.pack("!I", 3) + b"\x00" * 76
    with pytest.raises(InterfaceError, match="method 3.*password_encryption_type"):
        _core_stub().handle_AUTHENTICATION_REQUEST(payload, None)


def test_code_11_raises_actionable_error():
    payload = struct.pack("!i", 11) + b"\x00" * 8
    with pytest.raises(InterfaceError, match="MD5_SHA256.*iteration"):
        _core_stub().handle_AUTHENTICATION_REQUEST(payload, None)


def test_missing_password_raises():
    stub = _core_stub()
    stub.password = None
    payload = struct.pack("!i", 10) + _sha256_payload()
    with pytest.raises(InterfaceError, match="no password"):
        stub.handle_AUTHENTICATION_REQUEST(payload, None)


def test_scram_payload_delegates_to_stock_pg8000(monkeypatch):
    """PG's AuthenticationSASL is also code 10; the SCRAM shape must reach
    the stock handler so this class stays usable against real PostgreSQL."""
    seen = {}

    def fake_parent(self, data, context):
        seen["delegated"] = True

    monkeypatch.setattr(CoreConnection, "handle_AUTHENTICATION_REQUEST", fake_parent)
    payload = struct.pack("!i", 10) + b"SCRAM-SHA-256\x00\x00"
    _core_stub().handle_AUTHENTICATION_REQUEST(payload, None)
    assert seen.get("delegated") is True


def test_upstream_init_drift_guard():
    """The 3.51 startup rides on a clone of CoreConnection.__init__ whose
    only assumption is the ``i_pack(protocol)`` idiom. If upstream rewrites
    __init__, re-verify _pg8000_gauss against the new source and update the
    pinned hash."""
    src = inspect.getsource(CoreConnection.__init__)
    assert (
        hashlib.sha256(src.encode()).hexdigest() == "3354661aa84d010a910d697e7893edd0a321fc5a9c7a49c9b54efdf5a1cca291"
    ), "pg8000 CoreConnection.__init__ changed upstream; re-verify the protocol-3.51 clone"


def test_dbapi_module_surface():
    """SQLAlchemy's import_dbapi contract: the module must expose the DB-API
    names the dialect touches."""
    from datus_gaussdb import _pg8000_gauss as mod

    for name in (
        "connect",
        "Connection",
        "InterfaceError",
        "DatabaseError",
        "paramstyle",
    ):
        assert hasattr(mod, name), name
    assert issubclass(mod.Connection, CoreConnection)
