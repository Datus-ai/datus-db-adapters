"""Small MySQL wire peer for deterministic socket-timeout tests, without a database.

Only the handshake, SQLAlchemy initialization, and single-column results needed by
the tests are implemented. Selected queries receive no response. A safety timeout
closes the peer even when the client under test has no read deadline.
"""

import socket
import struct
import threading
from contextlib import AbstractContextManager
from typing import Callable


class StallingMySQLServer(AbstractContextManager):
    def __init__(
        self,
        stall: Callable[[str], bool],
        safety_timeout: float = 4,
        *,
        stall_handshake: bool = False,
        reject: Callable[[str], bool] | None = None,
    ):
        self.stall = stall
        self.stall_handshake = stall_handshake
        self.reject = reject
        self.safety_timeout = safety_timeout
        self.statements: list[str] = []
        self.connections = 0
        self.stalled = threading.Event()
        self.client_closed = threading.Event()
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen()
        self._listener.settimeout(0.1)
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(self.safety_timeout + 1)
        self._listener.close()
        if self._thread.is_alive():
            raise RuntimeError("MySQL wire peer did not stop")

    @staticmethod
    def _packet(conn, payload, sequence=0):
        conn.sendall(struct.pack("<I", len(payload))[:3] + bytes([sequence]) + payload)

    @staticmethod
    def _receive(conn):
        def exact(size):
            result = b""
            while len(result) < size:
                chunk = conn.recv(size - len(result))
                if not chunk:
                    raise EOFError
                result += chunk
            return result

        header = exact(4)
        return exact(int.from_bytes(header[:3], "little"))

    def _result(self, conn, value):
        # A protocol-4.1 text result with one VAR_STRING column and one row.
        column = b"\x03def\x00\x00\x00\x05value\x00\x0c\x21\x00\xff\x00\x00\x00\xfd\x00\x00\x00\x00\x00"
        eof = b"\xfe\x00\x00\x02\x00"
        row = b"\xfb" if value is None else bytes([len(value)]) + value
        for sequence, payload in enumerate((b"\x01", column, eof, row, eof), 1):
            self._packet(conn, payload, sequence)

    def _session(self, conn):
        if self.stall_handshake and not self.stalled.is_set():
            self._stall(conn)
            return
        capabilities = 0x00088205  # protocol 4.1, secure connection, plugin auth
        handshake = (
            b"\x0a8.0.36\x00"
            + struct.pack("<I", self.connections)
            + b"12345678\x00"
            + struct.pack("<H", capabilities & 0xFFFF)
            + b"\x2d\x02\x00"
            + struct.pack("<H", capabilities >> 16)
            + b"\x15"
            + b"\x00" * 10
            + b"abcdefghijkl\x00mysql_native_password\x00"
        )
        ok = b"\x00\x00\x00\x02\x00\x00\x00"
        self._packet(conn, handshake)
        self._receive(conn)
        self._packet(conn, ok, 2)
        while not self._stop.is_set():
            packet = self._receive(conn)
            if packet[0] == 1:  # COM_QUIT
                return
            if packet[0] != 3:  # COM_PING, etc.
                self._packet(conn, ok, 1)
                continue
            sql = packet[1:].decode()
            self.statements.append(sql)
            if not self.stalled.is_set() and self.stall(sql):
                self._stall(conn)
                return
            if self.reject is not None and self.reject(sql):
                self._packet(conn, b"\xff" + struct.pack("<H", 1193) + b"#HY000Unknown system variable", 1)
                continue
            normalized = sql.upper()
            if normalized.startswith("SELECT"):
                value = b"1"
                if "VERSION()" in normalized:
                    value = b"8.0.36"
                elif "DATABASE()" in normalized:
                    value = None
                elif "TRANSACTION_ISOLATION" in normalized:
                    value = b"REPEATABLE-READ"
                elif "SQL_MODE" in normalized:
                    value = b""
                elif "LOWER_CASE_TABLE_NAMES" in normalized:
                    value = b"0"
                self._result(conn, value)
            else:
                self._packet(conn, ok, 1)

    def _stall(self, conn):
        self.stalled.set()
        # A correctly bounded client closes this socket after its timeout.
        if conn.recv(1) == b"":
            self.client_closed.set()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            with conn:
                self.connections += 1
                conn.settimeout(self.safety_timeout)
                try:
                    self._session(conn)
                except (EOFError, OSError):
                    # Includes the intentional safety timeout on an unbounded client.
                    pass
