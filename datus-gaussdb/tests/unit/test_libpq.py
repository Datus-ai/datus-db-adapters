# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Tests for vendored libpq resolution.

Only the pure path/env resolution logic is exercised — no ctypes library is
ever loaded and the ``gaussdb`` driver is never imported.
"""

import ctypes.util
import platform
import sys

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
