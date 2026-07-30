from __future__ import annotations

import io
import json
import shlex
import subprocess
import urllib.error
from pathlib import Path

import pytest
from packaging.version import Version

from ci.resolve_package_publish import (
    PyPIReleaseStatus,
    next_patch_version,
    pypi_release_status,
    resolve_automatic_version,
    resolve_publish_state,
    resolve_requested_version,
    run_git,
)

PACKAGE = "datus-db-core"


def git(repo: Path, *args: str) -> str:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "<no output>"
        raise RuntimeError(f"{shlex.join(command)} failed with exit code {result.returncode}: {details}")
    return result.stdout.strip()


def write_package(repo: Path, version: str, dependencies: tuple[str, ...] = ()) -> None:
    package_dir = repo / PACKAGE
    package_dir.mkdir(exist_ok=True)
    dependency_lines = "\n".join(f'    "{dependency}",' for dependency in dependencies)
    (package_dir / "pyproject.toml").write_text(
        f'''[project]
name = "{PACKAGE}"
version = "{version}"
dependencies = [
{dependency_lines}
]
''',
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'''[tool.uv.workspace]
members = ["{PACKAGE}"]
''',
        encoding="utf-8",
    )
    write_package(tmp_path, "0.1.5")
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test User")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def no_latest_release(_package: str) -> Version | None:
    return None


def latest_release(version: str):
    def lookup(_package: str) -> Version:
        return Version(version)

    return lookup


def release_missing(_package: str, _version: Version) -> PyPIReleaseStatus:
    return PyPIReleaseStatus(exists=False, complete=False, artifact_types=())


def release_partial(_package: str, _version: Version) -> PyPIReleaseStatus:
    return PyPIReleaseStatus(exists=True, complete=False, artifact_types=("sdist",))


def release_complete(_package: str, _version: Version) -> PyPIReleaseStatus:
    return PyPIReleaseStatus(
        exists=True,
        complete=True,
        artifact_types=("bdist_wheel", "sdist"),
    )


def current_release_complete(_package: str, version: Version) -> PyPIReleaseStatus:
    if version == Version("0.1.5"):
        return release_complete(_package, version)
    return release_missing(_package, version)


def create_release_tag(
    repo: Path,
    version: str,
    dependencies: tuple[str, ...] = (),
) -> str:
    main_commit = git(repo, "rev-parse", "HEAD")
    write_package(repo, version, dependencies)
    git(repo, "add", ".")
    git(repo, "commit", "-m", f"release {version}")
    release_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", f"{PACKAGE}-v{version}", "-m", "release")
    git(repo, "checkout", "--detach", main_commit)
    git(repo, "branch", "-f", "main", main_commit)
    return release_commit


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("1", "1.0.1"),
        ("1.2", "1.2.1"),
        ("1.2.3", "1.2.4"),
    ],
)
def test_next_patch_version(current: str, expected: str) -> None:
    assert next_patch_version(Version(current)) == Version(expected)


def test_auto_version_rejects_non_final_release() -> None:
    with pytest.raises(ValueError, match="require a final release"):
        next_patch_version(Version("1.2.3rc1"))


def test_automatic_version_uses_current_for_first_release() -> None:
    assert resolve_automatic_version(Version("0.1.0"), None) == Version("0.1.0")


def test_automatic_version_uses_current_when_main_is_ahead() -> None:
    assert resolve_automatic_version(Version("0.1.5"), Version("0.1.4")) == Version("0.1.5")


def test_automatic_version_increments_published_main_version() -> None:
    assert resolve_automatic_version(Version("0.1.5"), Version("0.1.5")) == Version("0.1.6")


def test_automatic_version_reuses_release_when_post_publish_pr_is_pending() -> None:
    assert resolve_automatic_version(Version("0.1.5"), Version("0.1.6")) == Version("0.1.6")


def test_automatic_version_rejects_larger_pypi_gap() -> None:
    with pytest.raises(ValueError, match="provide the intended release version explicitly"):
        resolve_automatic_version(Version("0.1.5"), Version("0.2.0"))


def test_requested_version_overrides_auto_resolution() -> None:
    assert resolve_requested_version(Version("0.1.5"), Version("0.1.4"), "0.2.0") == Version("0.2.0")


@pytest.mark.parametrize("requested", ["", "auto", " AUTO "])
def test_auto_aliases_use_automatic_resolution(requested: str) -> None:
    assert resolve_requested_version(Version("0.1.5"), Version("0.1.4"), requested) == Version("0.1.5")


def test_run_git_exposes_stderr(repo: Path) -> None:
    with pytest.raises(RuntimeError, match="definitely-missing-ref"):
        run_git(repo, "rev-parse", "definitely-missing-ref")


def test_git_test_helper_exposes_stderr(repo: Path) -> None:
    with pytest.raises(RuntimeError, match="definitely-missing-ref"):
        git(repo, "rev-parse", "definitely-missing-ref")


def test_pypi_release_status_returns_missing_for_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_not_found(_url: str, timeout: int) -> io.BytesIO:
        assert timeout == 20
        raise urllib.error.HTTPError(_url, 404, "Not Found", None, None)

    monkeypatch.setattr("ci.resolve_package_publish.urllib.request.urlopen", raise_not_found)

    assert pypi_release_status(PACKAGE, Version("0.1.5")) == PyPIReleaseStatus(
        exists=False,
        complete=False,
        artifact_types=(),
    )


@pytest.mark.parametrize(
    ("published_name", "published_version"),
    [
        ("unexpected-package", "0.1.5"),
        (PACKAGE, "0.1.6"),
    ],
)
def test_pypi_release_status_rejects_unexpected_identity(
    monkeypatch: pytest.MonkeyPatch,
    published_name: str,
    published_version: str,
) -> None:
    payload = {
        "info": {"name": published_name, "version": published_version},
        "urls": [],
    }

    def respond(_url: str, timeout: int) -> io.BytesIO:
        assert timeout == 20
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("ci.resolve_package_publish.urllib.request.urlopen", respond)

    with pytest.raises(ValueError, match="Unexpected PyPI response"):
        pypi_release_status(PACKAGE, Version("0.1.5"))


@pytest.mark.parametrize(
    ("artifact_types", "complete"),
    [
        (("sdist",), False),
        (("bdist_wheel",), False),
        (("bdist_wheel", "sdist"), True),
    ],
)
def test_pypi_release_status_requires_wheel_and_sdist(
    monkeypatch: pytest.MonkeyPatch,
    artifact_types: tuple[str, ...],
    complete: bool,
) -> None:
    payload = {
        "info": {"name": PACKAGE, "version": "0.1.5"},
        "urls": [{"packagetype": artifact_type} for artifact_type in artifact_types],
    }

    def respond(_url: str, timeout: int) -> io.BytesIO:
        assert timeout == 20
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("ci.resolve_package_publish.urllib.request.urlopen", respond)

    status = pypi_release_status(PACKAGE, Version("0.1.5"))

    assert status.exists is True
    assert status.complete is complete
    assert status.artifact_types == tuple(sorted(artifact_types))


def test_new_release_can_publish_version_already_declared_on_main(repo: Path) -> None:
    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.4"),
        release_status=release_missing,
    )

    assert state.state == "new"
    assert state.current_version == "0.1.5"
    assert state.latest_pypi_version == "0.1.4"
    assert state.version == "0.1.5"
    assert state.release_commit == git(repo, "rev-parse", "HEAD")
    assert state.tag == f"{PACKAGE}-v0.1.5"
    assert state.branch == f"release/{PACKAGE}-0.1.5"
    assert state.pypi_exists is False
    assert state.pypi_complete is False


def test_new_first_release_uses_declared_main_version(repo: Path) -> None:
    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=no_latest_release,
        release_status=release_missing,
    )

    assert state.state == "new"
    assert state.latest_pypi_version == ""
    assert state.version == "0.1.5"


def test_explicit_release_version_is_used(repo: Path) -> None:
    state = resolve_publish_state(
        repo,
        PACKAGE,
        "0.2.0",
        latest_release=latest_release("0.1.4"),
        release_status=release_missing,
    )

    assert state.state == "new"
    assert state.version == "0.2.0"


def test_new_release_must_not_precede_main_version(repo: Path) -> None:
    with pytest.raises(ValueError, match="must not precede current"):
        resolve_publish_state(
            repo,
            PACKAGE,
            "0.1.4",
            latest_release=latest_release("0.1.4"),
            release_status=release_missing,
        )


def test_existing_tag_without_pypi_resumes_from_tagged_commit(repo: Path) -> None:
    release_commit = create_release_tag(repo, "0.1.6")

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.5"),
        release_status=release_missing,
    )

    assert state.state == "retry"
    assert state.release_commit == release_commit
    assert state.version == "0.1.6"


def test_partial_pypi_release_retries_from_tagged_commit(repo: Path) -> None:
    release_commit = create_release_tag(repo, "0.1.6")

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.6"),
        release_status=release_partial,
    )

    assert state.state == "retry"
    assert state.release_commit == release_commit
    assert state.pypi_exists is True
    assert state.pypi_complete is False


def test_partial_current_release_retries_even_when_tag_is_on_main(repo: Path) -> None:
    release_commit = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", f"{PACKAGE}-v0.1.5", "-m", "release")

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.5"),
        release_status=release_partial,
    )

    assert state.state == "retry"
    assert state.version == "0.1.5"
    assert state.release_commit == release_commit
    assert state.pypi_exists is True
    assert state.pypi_complete is False


def test_existing_tag_and_pypi_release_is_complete(repo: Path) -> None:
    create_release_tag(repo, "0.1.6")

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.6"),
        release_status=release_complete,
    )

    assert state.state == "complete"
    assert state.pypi_exists is True
    assert state.pypi_complete is True
    assert state.version == "0.1.6"


def test_complete_release_with_pending_metadata_pr_is_reused(repo: Path) -> None:
    release_commit = create_release_tag(repo, "0.1.5", ("example>=1",))

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.5"),
        release_status=release_complete,
    )

    assert state.state == "complete"
    assert state.version == "0.1.5"
    assert state.release_commit == release_commit


def test_merged_current_release_is_not_mistaken_for_pending_pr(repo: Path) -> None:
    release_commit = create_release_tag(repo, "0.1.5", ("example>=1",))
    git(repo, "checkout", "main")
    git(repo, "merge", "--ff-only", release_commit)
    write_package(repo, "0.1.5", ("example>=2",))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "advance unrelated release metadata")

    state = resolve_publish_state(
        repo,
        PACKAGE,
        latest_release=latest_release("0.1.5"),
        release_status=current_release_complete,
    )

    assert state.state == "new"
    assert state.version == "0.1.6"


def test_pypi_release_without_tag_requires_investigation(repo: Path) -> None:
    with pytest.raises(ValueError, match="release tag .* is missing"):
        resolve_publish_state(
            repo,
            PACKAGE,
            latest_release=latest_release("0.1.6"),
            release_status=release_complete,
        )


def test_tag_must_point_to_requested_package_version(repo: Path) -> None:
    git(repo, "tag", "-a", f"{PACKAGE}-v0.1.6", "-m", "wrong release")

    with pytest.raises(ValueError, match="points to .* 0.1.5, expected 0.1.6"):
        resolve_publish_state(
            repo,
            PACKAGE,
            latest_release=latest_release("0.1.5"),
            release_status=release_missing,
        )
