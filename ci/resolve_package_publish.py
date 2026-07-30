#!/usr/bin/env python3

"""Resolve a safe, resumable package publication state."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from packaging.version import Version

try:
    from package_release import (
        REPO_ROOT,
        load_workspace_packages,
        parse_canonical_version,
        require_package,
    )
except ModuleNotFoundError:  # Imported as ci.resolve_package_publish in tests.
    from ci.package_release import (
        REPO_ROOT,
        load_workspace_packages,
        parse_canonical_version,
        require_package,
    )


REQUIRED_PYPI_ARTIFACT_TYPES = frozenset({"bdist_wheel", "sdist"})


@dataclass(frozen=True)
class PyPIReleaseStatus:
    exists: bool
    complete: bool
    artifact_types: tuple[str, ...]


@dataclass(frozen=True)
class PublishState:
    package: str
    current_version: str
    latest_pypi_version: str
    version: str
    tag: str
    branch: str
    state: str
    release_commit: str
    pypi_exists: bool
    pypi_complete: bool


def run_git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    result = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "<no output>"
        raise RuntimeError(f"{shlex.join(command)} failed with exit code {result.returncode}: {details}")
    return result


def next_patch_version(current_version: Version) -> Version:
    if current_version.pre or current_version.dev or current_version.post or current_version.local:
        raise ValueError(f"Automatic patch increments require a final release version; found {current_version}")
    release = list(current_version.release)
    if len(release) > 3:
        raise ValueError(f"Automatic patch increments do not support {current_version}")
    while len(release) < 3:
        release.append(0)
    release[2] += 1
    return Version(".".join(str(part) for part in release))


def resolve_automatic_version(
    current_version: Version,
    latest_version: Version | None,
) -> Version:
    if latest_version is None or current_version > latest_version:
        return current_version
    if current_version == latest_version:
        return next_patch_version(current_version)

    expected_next = next_patch_version(current_version)
    if latest_version == expected_next:
        return latest_version
    raise ValueError(
        f"PyPI version {latest_version} is ahead of main version {current_version}; "
        "provide the intended release version explicitly"
    )


def resolve_requested_version(
    current_version: Version,
    latest_version: Version | None,
    requested_version: str,
) -> Version:
    requested_version = requested_version.strip()
    if requested_version and requested_version.lower() != "auto":
        return parse_canonical_version(requested_version)
    return resolve_automatic_version(current_version, latest_version)


def pypi_json(path: str) -> dict | None:
    url = f"https://pypi.org/pypi/{path}/json"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def latest_pypi_version(package_name: str) -> Version | None:
    payload = pypi_json(package_name)
    if payload is None:
        return None
    return Version(str(payload["info"]["version"]))


def pypi_release_status(package_name: str, version: Version) -> PyPIReleaseStatus:
    payload = pypi_json(f"{package_name}/{version}")
    if payload is None:
        return PyPIReleaseStatus(exists=False, complete=False, artifact_types=())

    published_name = str(payload.get("info", {}).get("name", ""))
    published_version = Version(str(payload.get("info", {}).get("version", "")))
    if published_name.lower() != package_name.lower() or published_version != version:
        raise ValueError(
            f"Unexpected PyPI response for {package_name} {version}: "
            f"found {published_name or '<missing>'} {published_version}"
        )

    artifact_types = tuple(
        sorted(
            {
                str(artifact.get("packagetype", ""))
                for artifact in payload.get("urls", [])
                if isinstance(artifact, dict) and artifact.get("packagetype")
            }
        )
    )
    complete = REQUIRED_PYPI_ARTIFACT_TYPES.issubset(artifact_types)
    return PyPIReleaseStatus(
        exists=True,
        complete=complete,
        artifact_types=artifact_types,
    )


def tag_commit(repo_root: Path, tag: str) -> str | None:
    result = run_git(repo_root, "rev-list", "-n", "1", tag, check=False)
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def release_commit_is_ancestor(repo_root: Path, commit: str) -> bool:
    result = run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        commit,
        "HEAD",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ValueError(f"Unable to determine whether release commit {commit} is on main")
    return result.returncode == 0


def package_version_at_commit(repo_root: Path, package_path: Path, commit: str) -> Version:
    relative_pyproject = package_path.relative_to(repo_root) / "pyproject.toml"
    result = run_git(repo_root, "show", f"{commit}:{relative_pyproject.as_posix()}")
    payload = tomllib.loads(result.stdout)
    project = payload.get("project", {})
    if not isinstance(project, dict) or "version" not in project:
        raise ValueError(f"{relative_pyproject} at {commit} has no project version")
    return Version(str(project["version"]))


def resolve_publish_state(
    repo_root: Path,
    package_name: str,
    requested_version: str = "",
    *,
    latest_release: Callable[[str], Version | None] = latest_pypi_version,
    release_status: Callable[[str, Version], PyPIReleaseStatus] = pypi_release_status,
) -> PublishState:
    repo_root = repo_root.resolve()
    packages = load_workspace_packages(repo_root)
    target = require_package(packages, package_name)
    latest_version = latest_release(target.name)
    automatic = not requested_version.strip() or requested_version.strip().lower() == "auto"
    current_tag = f"{target.name}-v{target.version}"
    current_tag_commit = tag_commit(repo_root, current_tag)
    current_release_status = (
        release_status(target.name, target.version)
        if automatic and latest_version == target.version and current_tag_commit is not None
        else None
    )
    pending_current_release = (
        automatic
        and latest_version == target.version
        and current_tag_commit is not None
        and (
            current_release_status is None
            or not current_release_status.complete
            or not release_commit_is_ancestor(repo_root, current_tag_commit)
        )
    )
    version = (
        target.version
        if pending_current_release
        else resolve_requested_version(target.version, latest_version, requested_version)
    )
    tag = f"{target.name}-v{version}"
    branch = f"release/{target.name}-{version}"
    existing_tag_commit = tag_commit(repo_root, tag)
    pypi_status = (
        current_release_status
        if current_release_status is not None and version == target.version
        else release_status(target.name, version)
    )

    if pypi_status.exists and existing_tag_commit is None:
        raise ValueError(
            f"{target.name} {version} exists on PyPI but release tag {tag} is missing; "
            "investigate the published artifact before repairing the tag manually"
        )

    if existing_tag_commit is not None:
        tagged_version = package_version_at_commit(repo_root, target.path, existing_tag_commit)
        if tagged_version != version:
            raise ValueError(f"Release tag {tag} points to {target.name} {tagged_version}, expected {version}")
        state = "complete" if pypi_status.complete else "retry"
        release_commit = existing_tag_commit
    else:
        if version < target.version:
            raise ValueError(
                f"Release version must not precede current {target.name} version {target.version}; got {version}"
            )
        state = "new"
        release_commit = run_git(repo_root, "rev-parse", "HEAD").stdout.strip()

    return PublishState(
        package=target.name,
        current_version=str(target.version),
        latest_pypi_version=str(latest_version or ""),
        version=str(version),
        tag=tag,
        branch=branch,
        state=state,
        release_commit=release_commit,
        pypi_exists=pypi_status.exists,
        pypi_complete=pypi_status.complete,
    )


def write_github_output(path: Path, state: PublishState) -> None:
    values = asdict(state)
    values["pypi_exists"] = str(state.pypi_exists).lower()
    values["pypi_complete"] = str(state.pypi_complete).lower()
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            print(f"{key}={value}", file=output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--package", required=True)
    parser.add_argument("--requested-version", default="")
    parser.add_argument(
        "--github-output",
        type=Path,
        default=Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        state = resolve_publish_state(args.repo_root, args.package, args.requested_version)
    except Exception as exc:
        print(f"Package publish state check failed: {exc}", file=sys.stderr)
        return 1

    if args.github_output is not None:
        write_github_output(args.github_output, state)
    print(json.dumps(asdict(state), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
