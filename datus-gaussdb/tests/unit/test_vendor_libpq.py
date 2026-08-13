# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Regression tests for the native-client vendoring inputs."""

import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PACKAGE_DIR / "scripts" / "fetch_vendor_libpq.py"


def _load_vendor_script():
    spec = importlib.util.spec_from_file_location("fetch_vendor_libpq", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.acceptance
def test_download_verifies_sha256(monkeypatch, tmp_path):
    script = _load_vendor_script()
    payload = b"pinned rpm payload"
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload))

    target = tmp_path / "openssl-libs.rpm"
    script.download("https://example.invalid/openssl-libs.rpm", expected_sha256, target)

    assert target.read_bytes() == payload


@pytest.mark.acceptance
def test_download_removes_file_on_sha256_mismatch(monkeypatch, tmp_path):
    script = _load_vendor_script()
    monkeypatch.setattr(script.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"changed"))

    target = tmp_path / "openssl-libs.rpm"
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        script.download("https://example.invalid/openssl-libs.rpm", "0" * 64, target)

    assert not target.exists()


@pytest.mark.acceptance
def test_checked_in_openssl_license_is_complete():
    license_text = (PACKAGE_DIR / "datus_gaussdb" / "_vendor" / "aarch64" / "OPENSSL-LICENSE.txt").read_text(
        encoding="utf-8"
    )

    assert "OpenSSL License" in license_text
    assert "Original SSLeay License" in license_text
    assert len(license_text) > 6_000
