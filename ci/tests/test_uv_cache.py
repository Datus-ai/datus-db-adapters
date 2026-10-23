"""A restored cache must retain usable sources without discarding healthy packages."""

import subprocess
from pathlib import Path

import pytest

from ci.uv_cache import incomplete_packages, repair_cache


def revision(cache: Path, package: str = "pyhive", version: str = "0.7.0") -> Path:
    root = cache / "sdists-v10" / "pypi" / package / version / "revision"
    root.mkdir(parents=True)
    (root / "metadata.msgpack").write_bytes(b"metadata")
    return root


@pytest.mark.parametrize("build_file", ["setup.py", "pyproject.toml"])
def test_complete_sources_and_built_wheels_are_preserved(tmp_path: Path, build_file: str) -> None:
    root = revision(tmp_path)
    (root / "src").mkdir()
    (root / "src" / build_file).write_text("# build definition")
    (root / "pyhive-0.7.0-py3-none-any.whl").write_bytes(b"wheel")

    assert incomplete_packages(tmp_path) == []


@pytest.mark.parametrize("source_state", ["empty", "missing", "wheel_only"])
def test_incomplete_sources_are_detected_even_when_a_wheel_remains(tmp_path: Path, source_state: str) -> None:
    root = revision(tmp_path)
    if source_state == "empty":
        (root / "src").mkdir()
    if source_state == "wheel_only":
        (root / "pyhive-0.7.0-py3-none-any.whl").write_bytes(b"wheel")

    assert incomplete_packages(tmp_path) == ["pyhive"]


def test_repair_cleans_only_incomplete_packages_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    revision(tmp_path)
    revision(tmp_path, version="0.6.5")
    healthy = revision(tmp_path, package="healthy") / "src"
    healthy.mkdir()
    (healthy / "setup.py").write_text("# valid")
    calls = []

    def clean(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True

    monkeypatch.setattr(subprocess, "run", clean)
    assert repair_cache(tmp_path) == ["pyhive"]
    assert calls == [["uv", "cache", "clean", "--cache-dir", str(tmp_path), "pyhive"]]
    assert (healthy / "setup.py").is_file()


def test_clean_failures_stop_cache_preparation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    revision(tmp_path)

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        repair_cache(tmp_path)


def test_empty_cache_needs_no_repair(tmp_path: Path) -> None:
    assert repair_cache(tmp_path / "not-created") == []
