# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Vendored libpq loading for the official ``gaussdb`` driver.

The ``gaussdb`` driver (psycopg3 fork) is pure Python but binds libpq via
ctypes, and its struct layouts match only the GaussDB/openGauss build of
libpq — loading a vanilla PostgreSQL libpq crashes the process. Wheels of
this package therefore bundle the openGauss libpq (Mulan PSL v2) plus its
OpenSSL dependencies under ``_vendor/<arch>/``, and this module makes the
driver load the bundled copy deterministically.

Resolution order:

1. ``DATUS_GAUSSDB_LIBPQ=system``   — skip vendoring, use system discovery
   (for hosts with the official GaussDB client installed).
2. ``DATUS_GAUSSDB_LIBPQ=/path/to/libpq.so`` — explicit library path.
3. Bundled library for the current platform, when present.
4. Fall back to system discovery (source checkouts have no ``_vendor``).
"""

import ctypes
import ctypes.util
import os
import platform
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from datus_db_core import get_logger

logger = get_logger(__name__)

_ENV_VAR = "DATUS_GAUSSDB_LIBPQ"
_VENDOR_DIR = Path(__file__).parent / "_vendor"

# Preload order matters: libpq's NEEDED entries must resolve against the
# bundled OpenSSL, not whatever the host ldconfig offers.
_PRELOAD_NAMES = ("libcrypto.so.1.1", "libssl.so.1.1")

_LIBPQ_NAMES = {"pq", "libpq.dylib", "libpq.dll"}


def _vendor_arch_dir() -> Optional[Path]:
    if sys.platform != "linux":
        return None
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(machine)
    if not arch:
        return None
    candidate = _VENDOR_DIR / arch
    return candidate if (candidate / "libpq.so.5").exists() else None


def resolve_libpq_path() -> Optional[str]:
    """Return the libpq path to force-load, or None for system discovery."""
    env = os.environ.get(_ENV_VAR, "").strip()
    if env.lower() == "system":
        return None
    if env:
        if Path(env).is_file():
            return env
        raise FileNotFoundError(f"{_ENV_VAR} must point to an existing library file: {env}")
    arch_dir = _vendor_arch_dir()
    if arch_dir:
        return str(arch_dir / "libpq.so.5")
    return None


@contextmanager
def _patched_find_library(libpq_path: str):
    original = ctypes.util.find_library

    def find_library(name):  # type: ignore[no-untyped-def]
        if name in _LIBPQ_NAMES:
            return libpq_path
        return original(name)

    ctypes.util.find_library = find_library
    try:
        yield
    finally:
        ctypes.util.find_library = original


def _reject_vanilla_postgres_libpq() -> None:
    """Refuse system discovery when it would bind a vanilla PostgreSQL libpq.

    The gaussdb driver's ctypes struct layouts match only the
    GaussDB/openGauss build of libpq; against a modern vanilla PostgreSQL
    libpq the first ``PQconninfoParse`` call segfaults the whole process
    (source checkouts without ``_vendor`` fall back to system discovery and
    hit exactly this on hosts with PostgreSQL installed). ``PQlibVersion()``
    is a safe probe — no arguments, plain int return: the GaussDB/openGauss
    libpq is a PostgreSQL 9.2 fork and reports 9xxxx, while any vanilla
    libpq found on a modern host reports >= 100000 (PostgreSQL 10+).
    """
    libpq_path = ctypes.util.find_library("pq")
    if libpq_path is None:
        # Nothing to probe; let the driver surface its own import error.
        return
    try:
        lib = ctypes.CDLL(libpq_path)
        version = int(lib.PQlibVersion())
    except (OSError, AttributeError):
        # Unloadable or ancient library: the driver's own loading will
        # produce a regular (non-crashing) error for these.
        return
    if version >= 100000:
        raise ImportError(
            f"libpq at {libpq_path!r} reports PostgreSQL {version // 10000}.x; the gaussdb "
            "driver requires the GaussDB/openGauss build of libpq and would crash the "
            "process against this one. Populate the vendored copy with "
            "`python scripts/fetch_vendor_libpq.py` (release wheels bundle it) or point "
            f"{_ENV_VAR} at a GaussDB client libpq."
        )


def import_gaussdb():
    """Import (and return) the ``gaussdb`` module against the right libpq.

    Must be the first import of ``gaussdb`` in the process — the driver
    binds libpq at import time.
    """
    if "gaussdb" in sys.modules:
        return sys.modules["gaussdb"]

    libpq_path = resolve_libpq_path()
    if libpq_path is None:
        _reject_vanilla_postgres_libpq()
        import gaussdb

        return gaussdb

    lib_dir = Path(libpq_path).parent
    for dep in _PRELOAD_NAMES:
        dep_path = lib_dir / dep
        if dep_path.exists():
            ctypes.CDLL(str(dep_path), mode=ctypes.RTLD_GLOBAL)

    logger.debug(f"Loading gaussdb driver with vendored libpq: {libpq_path}")
    with _patched_find_library(libpq_path):
        import gaussdb

    return gaussdb
