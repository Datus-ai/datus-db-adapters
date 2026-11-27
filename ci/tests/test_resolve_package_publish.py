from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest
from packaging.version import Version

from ci import publish_package_release as publisher
from ci.package_release import REPO_ROOT, load_workspace_packages
from ci.resolve_package_publish import (
    PublishState,
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


def write_package(repo: Path, version: str, dependencies: tuple[str, ...] = (), *, name: str = PACKAGE) -> None:
    package_dir = repo / name
    package_dir.mkdir(exist_ok=True)
    dependency_lines = "\n".join(f'    "{dependency}",' for dependency in dependencies)
    (package_dir / "pyproject.toml").write_text(
        f'''[project]
name = "{name}"
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


@pytest.fixture
def batch_repo(repo: Path) -> Path:
    dependencies = {
        PACKAGE: (),
        "datus-sqlalchemy": (PACKAGE,),
        "datus-mysql": ("datus-sqlalchemy",),
        "datus-starrocks": ("datus-sqlalchemy", "datus-mysql"),
        "datus-unrelated": (),
    }
    (repo / "pyproject.toml").write_text(f"[tool.uv.workspace]\nmembers = {json.dumps(list(dependencies))}\n")
    for package, upstream in dependencies.items():
        write_package(repo, "0.1.5", tuple(f"{name}>=0.1.5" for name in upstream), name=package)
    ci = repo / "ci"
    ci.mkdir()
    for filename in ("package_release.py", "prepare_package_release.py"):
        shutil.copy(REPO_ROOT / "ci" / filename, ci / filename)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "add adapter dependencies")
    return repo


@pytest.mark.parametrize(
    ("package", "checked", "recursive", "expected"),
    [
        (PACKAGE, [], False, [PACKAGE]),
        (PACKAGE, [], True, [PACKAGE, "datus-sqlalchemy", "datus-mysql", "datus-starrocks"]),
        (PACKAGE, ["datus-starrocks", "datus-mysql"], False, ["datus-mysql", "datus-starrocks"]),
    ],
)
def test_select_packages_deduplicates_and_orders_only_requested_releases(
    batch_repo: Path,
    package: str,
    checked: list[str],
    recursive: bool,
    expected: list[str],
) -> None:
    _, ordered = publisher.select_packages(batch_repo, package, checked, recursive)
    assert ordered == expected


@pytest.mark.parametrize(
    ("package", "checked", "recursive", "message"),
    [
        ("", [], False, "Choose a package"),
        (PACKAGE, ["datus-mysql", "datus-unknown"], False, "Unknown package 'datus-unknown'"),
        (PACKAGE, ["datus-mysql"], True, "Uncheck the adapter boxes"),
    ],
)
def test_select_packages_rejects_invalid_batches(
    batch_repo: Path, package: str, checked: list[str], recursive: bool, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        publisher.select_packages(batch_repo, package, checked, recursive)


def test_build_artifacts_excludes_uv_generated_gitignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    filenames = ["datus_db_core-0.1.6-py3-none-any.whl", "datus_db_core-0.1.6.tar.gz"]
    checked: list[str] = []

    def run(_repo: Path, *args: str) -> None:
        if args[:2] == ("uv", "build"):
            output_dir = Path(args[-1])
            output_dir.mkdir(parents=True)
            for filename in [".gitignore", *filenames]:
                (output_dir / filename).touch()
        if ("twine", "check") == args[2:4]:
            checked.extend(Path(arg).name for arg in args[4:])

    monkeypatch.setattr(publisher, "run", run)
    monkeypatch.setattr(publisher.subprocess, "check_output", lambda *args, **kwargs: "")

    artifacts = publisher.build_artifacts(tmp_path, PACKAGE, "0.1.6")

    assert checked == [Path(artifact).name for artifact in artifacts] == filenames


@pytest.mark.parametrize("dependent_bound", ["0.1.5", "0.1.6"], ids=["awaiting-metadata", "squash-merged"])
def test_auto_release_requires_recorded_dependency_bounds(
    batch_repo: Path, monkeypatch: pytest.MonkeyPatch, dependent_bound: str
) -> None:
    git(batch_repo, "checkout", "--detach")
    write_package(batch_repo, "0.1.6")
    write_package(batch_repo, "0.1.5", (f"{PACKAGE}>=0.1.6",), name="datus-sqlalchemy")
    git(batch_repo, "commit", "-am", "release 0.1.6")
    git(batch_repo, "tag", f"{PACKAGE}-v0.1.6")

    git(batch_repo, "checkout", "main")
    write_package(batch_repo, "0.1.6")
    write_package(batch_repo, "0.1.5", (f"{PACKAGE}>={dependent_bound}",), name="datus-sqlalchemy")
    git(batch_repo, "commit", "-am", "squash release metadata to main")

    def resolve(path: Path, name: str, version: str) -> PublishState:
        def status(_name: str, queried: Version) -> PyPIReleaseStatus:
            return release_complete(_name, queried) if queried == Version("0.1.6") else release_missing(_name, queried)

        return resolve_publish_state(path, name, version, latest_release=latest_release("0.1.6"), release_status=status)

    monkeypatch.setattr(publisher, "resolve_publish_state", resolve)

    state = publisher.resolve_release(batch_repo, PACKAGE, "auto")

    expected = ("0.1.7", "new") if dependent_bound == "0.1.6" else ("0.1.6", "complete")
    assert (state.version, state.state) == expected


@pytest.mark.parametrize(
    ("inputs", "expected_versions"),
    [
        pytest.param(
            {"package": PACKAGE, "recursive": True, "version": "0.1.7"},
            [(PACKAGE, "0.1.7"), ("datus-sqlalchemy", "0.1.6"), ("datus-mysql", "0.1.6"), ("datus-starrocks", "0.1.6")],
            id="recursive",
        ),
        pytest.param(
            {
                "package": "datus-unrelated",
                "recursive": False,
                "datus-starrocks": True,
                "datus-mysql": True,
                "datus-sqlalchemy": False,
            },
            [("datus-mysql", "0.1.6"), ("datus-starrocks", "0.1.6")],
            id="checkboxes",
        ),
    ],
)
def test_batch_build_failure_and_upload_retry_leave_consistent_release_state(
    batch_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inputs: dict[str, bool | str],
    expected_versions: list[tuple[str, str]],
) -> None:
    remote = tmp_path / "remote.git"
    git(batch_repo, "init", "--bare", str(remote))
    git(batch_repo, "remote", "add", "origin", str(remote))
    git(batch_repo, "push", "origin", "main")
    monkeypatch.setenv("GITHUB_REPOSITORY", "test/db-adapters")
    uploads: list[tuple[str, str]] = []
    verified: dict[str, Version] = {}
    pr_calls: list[list[str]] = []
    versions = dict(expected_versions)
    fix_file = "downstream-source-fix"

    def status(name: str, version: Version) -> PyPIReleaseStatus:
        return release_complete(name, version) if verified.get(name) == version else release_missing(name, version)

    def resolve(path: Path, name: str, version: str) -> PublishState:
        return resolve_publish_state(
            path,
            name,
            version,
            latest_release=lambda package: verified.get(package, Version("0.1.5")),
            release_status=status,
        )

    def build(path: Path, name: str, version: str) -> list[str]:
        if name == "datus-starrocks" and not (path / fix_file).exists():
            raise ValueError("downstream needs a source fix")
        package = load_workspace_packages(path)[name]
        assert package.version == Version(version)
        for dependency, requirement in package.dependencies.items():
            assert str(requirement.specifier) == f">={versions.get(dependency, '0.1.5')}"
        source_dir = tmp_path / "built" / name
        source_dir.mkdir(parents=True, exist_ok=True)
        artifacts = [source_dir / f"{name}.whl", source_dir / f"{name}.tar.gz"]
        for artifact in artifacts:
            artifact.write_text(f"{name} {version} validated artifact", encoding="utf-8")
        return [str(artifact) for artifact in artifacts]

    def upload(repo: Path, name: str, version: str, artifacts: list[str], _credentials: dict[str, str]) -> None:
        refs = git(repo, "ls-remote", "--tags", "origin", "refs/tags/*")
        assert all(f"refs/tags/{name}-v{version}" in refs for name, version in expected_versions)
        assert all(Path(artifact).read_text() == f"{name} {version} validated artifact" for artifact in artifacts)
        dependencies = load_workspace_packages(repo)[name].dependencies
        assert all(verified[dep] == Version(versions[dep]) for dep in dependencies if dep in versions)
        uploads.append((name, version))
        if name == "datus-starrocks" and uploads.count((name, version)) == 1:
            raise OSError("simulated upload failure after reserving all versions")
        verified[name] = Version(version)

    original_run = publisher.run
    original_check_output = subprocess.check_output

    def run(path: Path, *args: str, env: dict[str, str] | None = None) -> None:
        if args[:2] == ("gh", "pr"):
            assert verified == {name: Version(version) for name, version in expected_versions}
            pr_calls.append(list(args))
        else:
            original_run(path, *args, env=env)

    def check_output(args: list[str], **kwargs):
        if args[:3] == ["gh", "pr", "list"]:
            return "[]" if not pr_calls else '[{"number": 12, "state": "OPEN"}]'
        return original_check_output(args, **kwargs)

    monkeypatch.setattr(publisher, "resolve_publish_state", resolve)
    monkeypatch.setattr(publisher, "pypi_release_status", status)
    monkeypatch.setattr(publisher, "build_artifacts", build)
    monkeypatch.setattr(publisher, "publish_artifacts", upload)
    monkeypatch.setattr(publisher, "run", run)
    monkeypatch.setattr(publisher.subprocess, "check_output", check_output)

    git(batch_repo, "checkout", "--detach")
    with pytest.raises(ValueError, match="downstream needs a source fix"):
        publisher.prepare_release(batch_repo, inputs, "1234", tmp_path / "failed-artifact")
    assert not (tmp_path / "failed-artifact" / "releases.json").exists()
    assert git(batch_repo, "ls-remote", "--tags", "origin") == ""

    git(batch_repo, "checkout", "-f", "main")
    (batch_repo / fix_file).write_text("fixed", encoding="utf-8")
    git(batch_repo, "add", fix_file)
    git(batch_repo, "commit", "-m", "fix downstream build")
    git(batch_repo, "push", "origin", "main")
    git(batch_repo, "checkout", "--detach")
    artifact = tmp_path / "artifact"
    publisher.prepare_release(batch_repo, inputs, "1234", artifact)
    assert git(batch_repo, "ls-remote", "--tags", "origin") == ""
    assert not uploads

    release_repo = tmp_path / "publish-clone"
    git(tmp_path, "clone", "-b", "main", str(remote), str(release_repo))
    git(release_repo, "config", "user.name", "Test User")
    git(release_repo, "config", "user.email", "test@example.com")
    with pytest.raises(OSError, match="simulated upload failure"):
        publisher.publish_release(release_repo, "1234", artifact, {}, "")
    assert uploads == expected_versions
    assert not pr_calls
    final_tag = "datus-starrocks-v0.1.6"
    final_commit = git(release_repo, "rev-list", "-1", final_tag)

    git(batch_repo, "fetch", "--tags", "origin")
    git(batch_repo, "checkout", "--detach", "main")
    with pytest.raises(ValueError, match="reserved for its batch"):
        publisher.prepare_release(batch_repo, {"package": "datus-starrocks"}, "4321", tmp_path / "competing")
    rebuilt = tmp_path / "rebuilt"
    publisher.prepare_release(batch_repo, inputs, "1234", rebuilt)
    assert (rebuilt / "releases.json").read_text() == (artifact / "releases.json").read_text()
    assert [path.name for path in rebuilt.iterdir() if path.is_dir()] == ["datus-starrocks"]

    publisher.publish_release(release_repo, "1234", artifact, {}, "")
    assert uploads == [*expected_versions, expected_versions[-1]]
    assert git(release_repo, "rev-list", "-1", final_tag) == final_commit
    assert git(release_repo, "ls-remote", "--heads", "origin", "release/batch-1234").split()[0] == final_commit
    assert len(pr_calls) == 1
    assert pr_calls[0][2:7] == ["create", "--base", "main", "--head", "release/batch-1234"]

    git(release_repo, "merge", "--no-ff", final_commit, "-m", "merge batch release PR")
    git(release_repo, "push", "origin", "main")
    git(release_repo, "push", "origin", "--delete", "release/batch-1234")
    publisher.publish_release(release_repo, "1234", artifact, {}, "")
    assert uploads == [*expected_versions, expected_versions[-1]]
    assert len(pr_calls) == 1
    assert git(release_repo, "ls-remote", "--heads", "origin", "release/batch-1234") == ""


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
