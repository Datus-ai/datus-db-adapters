"""Host preflight must follow the selected container budget and keep headroom."""

import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts" / "prepare_express.py"))
CHECK_HOST = SCRIPT["check_host"]
CLEANUP = SCRIPT["cleanup_express"]


@pytest.fixture
def host(monkeypatch):
    import platform

    state = {
        "/proc/sys/vm/max_map_count": "135217728",
        "/proc/sys/fs/aio-max-nr": "262144",
        "/proc/meminfo": f"MemAvailable: {12 * 1024**2} kB\n",
    }
    read_text = Path.read_text

    def read_host_file(path, *args, **kwargs):
        if str(path) in state:
            return state[str(path)]
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(Path, "read_text", read_host_file)
    monkeypatch.setattr(shutil, "disk_usage", lambda root: SimpleNamespace(free=14 * 1024**3))
    monkeypatch.delenv("HANA_MEMORY_LIMIT_GIB", raising=False)
    monkeypatch.delenv("HANA_MIN_FREE_DISK_GIB", raising=False)
    return state


def test_minimal_trial_host_passes_preflight(host, tmp_path):
    CHECK_HOST(tmp_path)


def test_memory_override_keeps_headroom_above_container_cap(host, monkeypatch, tmp_path):
    monkeypatch.setenv("HANA_MEMORY_LIMIT_GIB", "12")
    with pytest.raises(RuntimeError, match="12 GiB container limit plus 2 GiB host headroom"):
        CHECK_HOST(tmp_path)


def test_disk_budget_is_configurable(host, monkeypatch, tmp_path):
    monkeypatch.setenv("HANA_MIN_FREE_DISK_GIB", "30")
    with pytest.raises(RuntimeError, match="Free disk 14.0 GiB.*trial budget 30 GiB"):
        CHECK_HOST(tmp_path)


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("HANA_MEMORY_LIMIT_GIB", "4", "at least 8"),
        ("HANA_MIN_FREE_DISK_GIB", "0", "must be positive"),
    ],
)
def test_invalid_resource_budget_fails_explicitly(host, monkeypatch, tmp_path, variable, value, message):
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValueError, match=message):
        CHECK_HOST(tmp_path)


@pytest.mark.parametrize(("key", "value"), [("vm/max_map_count", "1048576"), ("fs/aio-max-nr", "65536")])
def test_kernel_limit_diagnostic_is_independent_of_resource_budget(host, tmp_path, key, value):
    host[f"/proc/sys/{key}"] = value
    with pytest.raises(RuntimeError, match=f"{key}={value}"):
        CHECK_HOST(tmp_path)


def test_explicit_sysctl_skip_allows_trial_with_existing_kernel_limits(host, tmp_path):
    host["/proc/sys/vm/max_map_count"] = "1048576"
    host["/proc/sys/fs/aio-max-nr"] = "65536"
    CHECK_HOST(tmp_path, skip_sysctl_check=True)


def test_sysctl_skip_still_enforces_resource_budget(host, monkeypatch, tmp_path):
    monkeypatch.setenv("HANA_MIN_FREE_DISK_GIB", "30")
    with pytest.raises(RuntimeError, match="trial budget 30 GiB"):
        CHECK_HOST(tmp_path, skip_sysctl_check=True)


def test_cleanup_removes_only_this_checkouts_express_state(tmp_path):
    runtime = tmp_path / ".express"
    (runtime / "data").mkdir(parents=True)
    (runtime / "data/database.dat").write_bytes(b"test database")
    source = tmp_path / "docker-compose.yml"
    source.write_text("services: {}")
    CLEANUP(tmp_path)
    assert not runtime.exists()
    assert source.read_text() == "services: {}"
    CLEANUP(tmp_path)  # A repeated CI cleanup is harmless.


def test_cleanup_refuses_symlink_and_preserves_its_target(tmp_path):
    target = tmp_path / "other-data"
    target.mkdir()
    (target / "keep").write_text("preserved")
    (tmp_path / ".express").symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink or mounted"):
        CLEANUP(tmp_path)
    assert (target / "keep").read_text() == "preserved"


def test_cleanup_refuses_a_mounted_filesystem(tmp_path, monkeypatch):
    runtime = tmp_path / ".express"
    runtime.mkdir()
    monkeypatch.setattr(Path, "is_mount", lambda path: path == runtime)
    with pytest.raises(RuntimeError, match="symlink or mounted"):
        CLEANUP(tmp_path)
    assert runtime.exists()


@pytest.mark.parametrize("nested", [False, True])
def test_cleanup_refuses_linux_bind_mounts_and_nested_mounts(tmp_path, monkeypatch, nested):
    root = tmp_path / "checkout with spaces"
    runtime = root / ".express"
    runtime.mkdir(parents=True)
    location = runtime / "data" if nested else runtime
    encoded = str(location).replace(" ", r"\040")
    read_text = Path.read_text

    def read_mountinfo(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            return f"42 0 0:9 / {encoded} rw - ext4 /dev/loop999 rw\n"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_mountinfo)
    monkeypatch.setattr(Path, "is_mount", lambda path: False)
    with pytest.raises(RuntimeError, match="mounted Express directory"):
        CLEANUP(root)
    assert runtime.exists()
