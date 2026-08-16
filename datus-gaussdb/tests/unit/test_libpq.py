# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Tests for vendored libpq resolution.

No native library or real ``gaussdb`` driver is loaded; import and ctypes
boundaries are mocked when the selected-library path is exercised.
"""

import builtins
import ctypes.util
import platform
import sys
from contextlib import contextmanager

import pytest

from datus_gaussdb import _libpq

_ENV_VAR = "DATUS_GAUSSDB_LIBPQ"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from an unset override so host config cannot leak in."""
    monkeypatch.delenv(_ENV_VAR, raising=False)


def _fake_vendor_dir(tmp_path, arch: str = "x86_64"):
    """Create a vendor layout containing a stub bundled libpq."""
    arch_dir = tmp_path / arch
    arch_dir.mkdir()
    (arch_dir / "libpq.so.5").write_bytes(b"")
    return arch_dir


def _pretend_linux(monkeypatch, machine: str = "x86_64"):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: machine)


# ==================== resolve_libpq_path ====================


@pytest.mark.acceptance
def test_resolve_libpq_path_system_opts_out(monkeypatch, tmp_path):
    """'system' skips vendoring even when a bundled library is present."""
    monkeypatch.setenv(_ENV_VAR, "system")
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path)
    _fake_vendor_dir(tmp_path)
    _pretend_linux(monkeypatch)

    assert _libpq.resolve_libpq_path() is None


@pytest.mark.acceptance
def test_resolve_libpq_path_system_is_case_insensitive(monkeypatch):
    """The opt-out keyword is matched case-insensitively."""
    monkeypatch.setenv(_ENV_VAR, "  SYSTEM  ")

    assert _libpq.resolve_libpq_path() is None


@pytest.mark.acceptance
def test_resolve_libpq_path_explicit_path(monkeypatch, tmp_path):
    """An explicit, existing path wins over the bundled library."""
    explicit = tmp_path / "libpq.so.5"
    explicit.write_bytes(b"")
    monkeypatch.setenv(_ENV_VAR, str(explicit))
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path / "vendor")

    assert _libpq.resolve_libpq_path() == str(explicit)


@pytest.mark.acceptance
def test_resolve_libpq_path_explicit_path_missing(monkeypatch, tmp_path):
    """A typo'd override fails loudly instead of silently falling back."""
    monkeypatch.setenv(_ENV_VAR, str(tmp_path / "nope" / "libpq.so.5"))

    with pytest.raises(FileNotFoundError, match=_ENV_VAR):
        _libpq.resolve_libpq_path()


@pytest.mark.acceptance
def test_resolve_libpq_path_without_vendor_dir(monkeypatch, tmp_path):
    """Source checkouts have no _vendor, so system discovery is used."""
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path / "missing_vendor")
    _pretend_linux(monkeypatch)

    assert _libpq.resolve_libpq_path() is None


@pytest.mark.acceptance
def test_resolve_libpq_path_uses_bundled_library(monkeypatch, tmp_path):
    """A wheel with a matching bundled arch resolves to the bundled libpq."""
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path)
    arch_dir = _fake_vendor_dir(tmp_path)
    _pretend_linux(monkeypatch)

    assert _libpq.resolve_libpq_path() == str(arch_dir / "libpq.so.5")


@pytest.mark.acceptance
def test_resolve_libpq_path_normalizes_arch_aliases(monkeypatch, tmp_path):
    """'arm64' and 'aarch64' name the same bundled directory."""
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path)
    arch_dir = _fake_vendor_dir(tmp_path, arch="aarch64")
    _pretend_linux(monkeypatch, machine="arm64")

    assert _libpq.resolve_libpq_path() == str(arch_dir / "libpq.so.5")


@pytest.mark.acceptance
def test_resolve_libpq_path_ignores_unknown_arch(monkeypatch, tmp_path):
    """An unbundled architecture falls back to system discovery."""
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path)
    _fake_vendor_dir(tmp_path)
    _pretend_linux(monkeypatch, machine="ppc64le")

    assert _libpq.resolve_libpq_path() is None


@pytest.mark.acceptance
def test_resolve_libpq_path_ignores_vendor_dir_off_linux(monkeypatch, tmp_path):
    """The bundled .so is Linux-only; macOS/Windows use system discovery."""
    monkeypatch.setattr(_libpq, "_VENDOR_DIR", tmp_path)
    _fake_vendor_dir(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert _libpq.resolve_libpq_path() is None


# ==================== _patched_find_library ====================


@pytest.mark.acceptance
def test_patched_find_library_redirects_only_libpq(monkeypatch):
    """Only the libpq aliases are redirected; other lookups keep working."""
    seen = []

    def original(name):
        seen.append(name)
        return f"/system/lib/{name}.so"

    monkeypatch.setattr(ctypes.util, "find_library", original)

    with _libpq._patched_find_library("/bundled/libpq.so.5"):
        assert ctypes.util.find_library("pq") == "/bundled/libpq.so.5"
        assert ctypes.util.find_library("libpq.dylib") == "/bundled/libpq.so.5"
        assert ctypes.util.find_library("libpq.dll") == "/bundled/libpq.so.5"
        assert ctypes.util.find_library("ssl") == "/system/lib/ssl.so"

    assert seen == ["ssl"]


@pytest.mark.acceptance
def test_patched_find_library_restores_original(monkeypatch):
    """The global ctypes hook is restored even when the body raises."""

    def original(name):
        return None

    monkeypatch.setattr(ctypes.util, "find_library", original)

    with pytest.raises(RuntimeError, match="driver import failed"):
        with _libpq._patched_find_library("/bundled/libpq.so.5"):
            assert ctypes.util.find_library("pq") == "/bundled/libpq.so.5"
            raise RuntimeError("driver import failed")

    assert ctypes.util.find_library is original


# ==================== import_gaussdb ====================


@pytest.mark.acceptance
def test_import_gaussdb_returns_already_imported_module(monkeypatch):
    """A previously imported driver is reused; libpq is not resolved again."""
    sentinel = object()
    monkeypatch.setitem(sys.modules, "gaussdb", sentinel)
    monkeypatch.setattr(
        _libpq,
        "resolve_libpq_path",
        lambda: pytest.fail("libpq must not be resolved once gaussdb is imported"),
    )

    assert _libpq.import_gaussdb() is sentinel


@pytest.mark.acceptance
def test_import_gaussdb_uses_system_discovery_without_preloading(monkeypatch):
    """System discovery imports the driver without patching or preloading."""
    sentinel = object()
    original_import = builtins.__import__
    monkeypatch.delitem(sys.modules, "gaussdb", raising=False)
    monkeypatch.setattr(_libpq, "resolve_libpq_path", lambda: None)
    # No discoverable libpq: the vanilla-PostgreSQL probe has nothing to
    # inspect and must fall through to the driver's own import.
    monkeypatch.setattr(ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(
        _libpq.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: pytest.fail("system discovery must not preload bundled libraries"),
    )
    monkeypatch.setattr(
        _libpq,
        "_patched_find_library",
        lambda *_args, **_kwargs: pytest.fail("system discovery must not patch find_library"),
    )

    def fake_import(name, *args, **kwargs):
        if name == "gaussdb":
            return sentinel
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _libpq.import_gaussdb() is sentinel


@pytest.mark.acceptance
def test_import_gaussdb_preloads_openssl_and_patches_libpq_lookup(monkeypatch, tmp_path):
    """A selected libpq loads its OpenSSL pair before the wrapped driver import."""
    sentinel = object()
    original_import = builtins.__import__
    events = []
    for library in ("libpq.so.5", "libcrypto.so.1.1", "libssl.so.1.1"):
        (tmp_path / library).write_bytes(b"")

    monkeypatch.delitem(sys.modules, "gaussdb", raising=False)
    monkeypatch.setattr(_libpq, "resolve_libpq_path", lambda: str(tmp_path / "libpq.so.5"))
    monkeypatch.setattr(
        _libpq.ctypes,
        "CDLL",
        lambda path, mode: events.append(("preload", path, mode)),
    )

    @contextmanager
    def patched_find_library(path):
        events.append(("patch-enter", path))
        try:
            yield
        finally:
            events.append(("patch-exit", path))

    monkeypatch.setattr(_libpq, "_patched_find_library", patched_find_library)

    def fake_import(name, *args, **kwargs):
        if name == "gaussdb":
            events.append(("import", name))
            return sentinel
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _libpq.import_gaussdb() is sentinel
    assert events == [
        ("preload", str(tmp_path / "libcrypto.so.1.1"), ctypes.RTLD_GLOBAL),
        ("preload", str(tmp_path / "libssl.so.1.1"), ctypes.RTLD_GLOBAL),
        ("patch-enter", str(tmp_path / "libpq.so.5")),
        ("import", "gaussdb"),
        ("patch-exit", str(tmp_path / "libpq.so.5")),
    ]


# ==================== vanilla-libpq guard ====================


class _FakeLibpq:
    def __init__(self, version: int):
        self._version = version

    def PQlibVersion(self) -> int:  # noqa: N802 - mirrors the C symbol
        return self._version


def _system_discovery(monkeypatch, found: str | None, cdll):
    """Route import_gaussdb into system discovery with a fake probe target."""
    monkeypatch.delitem(sys.modules, "gaussdb", raising=False)
    monkeypatch.setattr(_libpq, "resolve_libpq_path", lambda: None)
    monkeypatch.setattr(ctypes.util, "find_library", lambda _name: found)
    monkeypatch.setattr(_libpq.ctypes, "CDLL", cdll)


@pytest.mark.acceptance
def test_import_gaussdb_rejects_vanilla_postgres_libpq(monkeypatch):
    """A discoverable PostgreSQL 10+ libpq raises instead of segfaulting.

    Regression guard for the nightly crash: a source checkout without
    `_vendor` falls back to system discovery, finds the host's vanilla
    libpq, and the driver's first PQconninfoParse call segfaults. The
    probe must turn that into an actionable ImportError.
    """
    _system_discovery(monkeypatch, "libpq.so.5", lambda _path: _FakeLibpq(170005))

    with pytest.raises(ImportError, match="fetch_vendor_libpq"):
        _libpq.import_gaussdb()


@pytest.mark.acceptance
def test_import_gaussdb_accepts_gaussdb_family_libpq(monkeypatch):
    """A GaussDB/openGauss libpq (PostgreSQL 9.2 fork) passes the probe."""
    sentinel = object()
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gaussdb":
            return sentinel
        return original_import(name, *args, **kwargs)

    _system_discovery(monkeypatch, "libpq.so.5", lambda _path: _FakeLibpq(90204))
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _libpq.import_gaussdb() is sentinel


@pytest.mark.acceptance
def test_import_gaussdb_probe_tolerates_unloadable_library(monkeypatch):
    """An unloadable probe target falls through to the driver's own error."""
    sentinel = object()
    original_import = builtins.__import__

    def raising_cdll(_path):
        raise OSError("cannot open shared object file")

    def fake_import(name, *args, **kwargs):
        if name == "gaussdb":
            return sentinel
        return original_import(name, *args, **kwargs)

    _system_discovery(monkeypatch, "libpq.so.5", raising_cdll)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _libpq.import_gaussdb() is sentinel
