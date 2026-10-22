"""HS2 fixture I/O must preserve UTF-8 independently of the host locale."""

import io
import json
from unittest.mock import MagicMock

import pytest

from datus_db_core.testing import hs2


@pytest.fixture
def non_utf8_locale(monkeypatch):
    original = io.open
    encodings = {}

    def open_with_locale(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        if "b" not in mode:
            encodings[str(file)] = encoding
            encoding = encoding or "cp1252"
        return original(file, mode, buffering, encoding, *args, **kwargs)

    monkeypatch.setattr(io, "open", open_with_locale)
    return encodings


@pytest.mark.parametrize(
    "loader,contents",
    [(hs2.load, "label: 中文\n"), (hs2.load_json, '{"label":"中文"}')],
)
def test_fixture_loaders_use_utf8(tmp_path, non_utf8_locale, loader, contents):
    path = tmp_path / "fixture.txt"
    path.write_bytes(contents.encode("utf-8"))
    assert loader(path) == {"label": "中文"}
    assert non_utf8_locale[str(path)] == "utf-8"


def test_report_writer_uses_utf8(tmp_path, monkeypatch, non_utf8_locale):
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "fixture.yaml").write_bytes("label: 中文\n".encode("utf-8"))
    reports = tmp_path / "reports"
    monkeypatch.setenv("HS2_TEST_REPORT_DIR", str(reports))
    monkeypatch.setattr(hs2.subprocess, "check_output", lambda *args, **kwargs: "")
    environment = hs2.Environment.__new__(hs2.Environment)
    environment.root = root
    environment.input_hash = hs2.content_hash(root)
    environment.backend = "adapter"
    environment.name = "inceptor_ce_v2"
    environment.started = 1
    environment.server_version = "中文版本"
    environment.profile = {"configuration": {}}
    environment.databases = {}
    environment.checks = []
    environment.connector = MagicMock()

    environment.close()

    path = reports / "adapter-inceptor_ce_v2-1.json"
    assert non_utf8_locale[str(path)] == "utf-8"
    report = json.loads(path.read_bytes().decode("utf-8"))
    assert report["server_version"] == "中文版本"
    assert report["checks"] == []
    assert report["cleanup_errors"] == []
    environment.connector.close.assert_called_once_with()
