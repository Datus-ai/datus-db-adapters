# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Accept a CA certificate as inline PEM, not only as a path on disk.

A hosted caller uploads the certificate through a browser, so what reaches the
adapter is the file's *content*: there is no operator to drop a file on the
server first, and on a multi-tenant runtime there is nowhere stable to drop it.

psycopg2 takes a filename rather than bytes, so inline PEM is spilled to a
private temp file, once per distinct certificate and reused thereafter.
"""

import atexit
import hashlib
import os
import tempfile
import threading
from typing import Dict, Optional

from datus_db_core import get_logger

logger = get_logger(__name__)

_PEM_MARKER = "-----BEGIN CERTIFICATE-----"

_lock = threading.Lock()
_materialized: Dict[str, str] = {}


def is_inline_pem(value: Optional[str]) -> bool:
    """Whether *value* is certificate content rather than a path to one."""
    return bool(value) and _PEM_MARKER in value


def as_path(value: Optional[str]) -> Optional[str]:
    """Return a filesystem path for *value*, writing it out if it is inline PEM.

    Paths pass through untouched, so a self-hosted deployment that mounts its
    CA file keeps working exactly as before.
    """
    if not is_inline_pem(value):
        return value

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    with _lock:
        path = _materialized.get(digest)
        if path and os.path.exists(path):
            return path

        fd, path = tempfile.mkstemp(prefix=f"datus-dws-ca-{digest[:12]}-", suffix=".pem")
        try:
            # 0600 before the bytes land: a world-readable trust anchor is an
            # invitation to swap it.
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
        except BaseException:
            os.unlink(path)
            raise

        _materialized[digest] = path
        logger.debug("Materialized inline DWS CA certificate to %s", path)
        return path


@atexit.register
def _cleanup() -> None:
    with _lock:
        for path in _materialized.values():
            try:
                os.unlink(path)
            except OSError:
                pass
        _materialized.clear()
