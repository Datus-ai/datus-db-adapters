"""Local TCP fault proxy for integration tests against real databases."""

import selectors
import socket
import threading
import time
from contextlib import AbstractContextManager


class BlackholeProxy(AbstractContextManager):
    """Forward a real protocol connection, optionally discarding server replies."""

    def __init__(self, host, port, safety_timeout=6):
        self.target = (host, port)
        self.safety_timeout = safety_timeout
        self.drop_responses = threading.Event()
        self._stop = threading.Event()
        self._workers = []
        self._sockets = []
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen()
        self._listener.settimeout(0.1)
        self._thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join(1)
        self._listener.close()
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in self._workers:
            worker.join(1)
        assert not any(worker.is_alive() for worker in self._workers), "Fault proxy leaked a worker"

    def _accept(self):
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            worker = threading.Thread(target=self._forward, args=(client,), daemon=True)
            self._workers.append(worker)
            worker.start()

    def _forward(self, client):
        with client:
            try:
                upstream = socket.create_connection(self.target, timeout=3)
            except OSError:
                return
            with upstream, selectors.DefaultSelector() as selector:
                self._sockets.extend((client, upstream))
                upstream.settimeout(1)
                client.settimeout(1)
                selector.register(client, selectors.EVENT_READ, upstream)
                selector.register(upstream, selectors.EVENT_READ, client)
                silent_since = None
                while not self._stop.is_set():
                    if self.drop_responses.is_set():
                        silent_since = silent_since or time.monotonic()
                        # Independently unblock a broken adapter, so the test's
                        # stricter elapsed-time assertion fails instead of hanging.
                        if time.monotonic() - silent_since >= self.safety_timeout:
                            return
                    else:
                        silent_since = None
                    for key, _ in selector.select(0.1):
                        try:
                            data = key.fileobj.recv(65536)
                            if not data:
                                return
                            if key.fileobj is upstream and self.drop_responses.is_set():
                                continue
                            key.data.sendall(data)
                        except OSError:
                            return
