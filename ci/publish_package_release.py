"""Publish workspace packages from a single GitHub Actions run."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from zipfile import ZipFile

from packaging.utils import canonicalize_name
from packaging.version import Version

from ci.package_release import (
    REPO_ROOT,
    dependency_build_order,
    dependent_packages,
    load_workspace_packages,
    lower_bound,
    parse_canonical_version,
    require_package,
)
from ci.resolve_package_publish import (
    PublishState,
    next_patch_version,
    package_version_at_commit,
    pypi_release_status,
    resolve_publish_state,
    run_git,
    tag_commit,
)


@dataclass(frozen=True)
class Release:
    package: str
    version: str
    commit: str

    @property
    def tag(self) -> str:
        return f"{self.package}-v{self.version}"


def select_packages(repo: Path, package: str, checked: list[str], recursive: bool) -> tuple[list[str], list[str]]:
    workspace = load_workspace_packages(repo)
    if recursive and checked:
        raise ValueError("Use the package dropdown alone for recursive release. Uncheck the adapter boxes.")
    if not checked and not package:
        raise ValueError("Choose a package from the dropdown or check the adapter boxes.")
    roots = [require_package(workspace, name).name for name in checked or [package]]

    selected = set(roots)
    if recursive:
        pending = roots.copy()
        while pending:
            for dependent in dependent_packages(workspace, require_package(workspace, pending.pop())):
                if dependent.name not in selected:
                    selected.add(dependent.name)
                    pending.append(dependent.name)

    ordered = dict.fromkeys(
        dependency.name
        for name in sorted(selected)
        for dependency in dependency_build_order(workspace, require_package(workspace, name))
        if dependency.name in selected
    )
    return roots, list(ordered)


def run(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    print(f"+ {shlex.join(args)}", flush=True)
    subprocess.run(args, cwd=repo, env=env, check=True)


def summarize(markdown: str) -> None:
    print(markdown, flush=True)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"{markdown}\n")


def prepare_metadata(repo: Path, package: str, version: str) -> None:
    run(
        repo,
        sys.executable,
        "ci/prepare_package_release.py",
        "--package",
        package,
        "--version",
        version,
        "--update-dependent-bounds",
        "--allow-current-version",
        "--allow-no-changes",
    )


def distribution_files(directory: Path, package: str) -> list[str]:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if not wheels or not sdists:
        raise ValueError(f"Expected both a wheel and an sdist for {package}.")
    return [str(path) for path in [*wheels, *sdists]]


def build_artifacts(repo: Path, package: str, version: str) -> list[str]:
    run(
        repo,
        sys.executable,
        "ci/check_package_release_readiness.py",
        "--package",
        package,
        "--expected-version",
        version,
        "--check-dependent-bounds",
        "--build",
    )
    targets = subprocess.check_output(["ci/run-unit-tests.sh", "--list"], cwd=repo, text=True)
    if package in {line.split()[0] for line in targets.splitlines() if line.split()}:
        run(repo, "ci/run-unit-tests.sh", package)
    else:
        print(f"No package-scoped unit-test target configured for {package}", flush=True)
    run(repo, "git", "diff", "--check")

    if package == "datus-gaussdb":
        run(repo, sys.executable, "datus-gaussdb/scripts/fetch_vendor_libpq.py", "--arch", "all")

    release_dir = repo / "dist" / "release" / package
    if release_dir.exists():
        shutil.rmtree(release_dir)
    run(repo, "uv", "build", "--package", package, "--out-dir", str(release_dir))
    artifacts = distribution_files(release_dir, package)
    run(repo, sys.executable, "-m", "twine", "check", *artifacts)

    if package == "datus-gaussdb":
        required = {
            f"datus_gaussdb/_vendor/{arch}/{filename}"
            for arch in ("x86_64", "aarch64")
            for filename in (
                "libpq.so.5",
                "libssl.so.1.1",
                "libcrypto.so.1.1",
                "MulanPSL-2.0.txt",
                "OPENSSL-LICENSE.txt",
            )
        }
        for wheel in release_dir.glob("*.whl"):
            with ZipFile(wheel) as archive:
                if missing := required.difference(archive.namelist()):
                    raise ValueError(f"{wheel.name} is missing vendored files: {sorted(missing)}")
    return artifacts


def publish_artifacts(
    repo: Path, package: str, version: str, artifacts: list[str], credentials: dict[str, str]
) -> None:
    password = credentials["PYPI_PASSWORD"] or credentials["PYPI_API_TOKEN"] or credentials["PYPI_TOKEN"]
    if not password:
        raise ValueError("Missing PyPI password/token secret.")
    username = credentials["PYPI_USERNAME"] or ("transform_data" if credentials["PYPI_PASSWORD"] else "__token__")
    run(
        repo,
        sys.executable,
        "-m",
        "twine",
        "upload",
        "--non-interactive",
        "--skip-existing",
        *artifacts,
        env={**os.environ, "TWINE_USERNAME": username, "TWINE_PASSWORD": password},
    )
    for attempt in range(12):
        try:
            status = pypi_release_status(package, Version(version))
            if not status.complete:
                raise ValueError(f"PyPI release is missing required artifacts; found {status.artifact_types}")
            return
        except Exception as exc:
            if attempt == 11:
                raise
            print(f"PyPI verification attempt {attempt + 1} failed for {package} {version}: {exc}", flush=True)
            time.sleep(10)


def commit_metadata(repo: Path, message: str, *, always_commit: bool = False) -> None:
    run(repo, "git", "add", "--", ":(glob)*/pyproject.toml")
    if always_commit or run_git(repo, "diff", "--cached", "--name-only").stdout:
        run(repo, "git", "commit", "--allow-empty", "-m", message)


def push_release_refs(
    repo: Path, refs: list[str], *, branch: str | None = None, commit: str = "", update_branch: bool = False
) -> None:
    args = ["--atomic"]
    if branch:
        branch_ref = f"refs/heads/{branch}"
        if update_branch:
            remote = run_git(repo, "ls-remote", "--refs", "origin", branch_ref).stdout.split()
            args.append(f"--force-with-lease={branch_ref}:{remote[0] if remote else ''}")
        refs = [*refs, f"{commit}:{branch_ref}"]
    if refs:
        run(repo, "git", "push", *args, "origin", *refs)


def sync_release_pr(
    repo: Path,
    branch: str,
    commit: str,
    title: str,
    body: str,
    gh_token: str,
    *,
    batch: bool = False,
) -> None:
    run(repo, "git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main")
    comparison = [f"origin/main...{commit}"] if batch else ["origin/main", commit]
    diff = run_git(repo, "diff", "--quiet", *comparison, "--", ":(glob)*/pyproject.toml", check=False)
    if diff.returncode == 0:
        print("Release metadata is already present on main; no PR is needed.", flush=True)
        return
    if diff.returncode != 1:
        raise RuntimeError(f"Unable to compare release metadata with main: {diff.stderr.strip()}")

    env = {**os.environ, "GH_TOKEN": gh_token}
    repository = os.environ["GITHUB_REPOSITORY"]
    prs = json.loads(
        subprocess.check_output(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--base",
                "main",
                "--head",
                branch,
                "--state",
                "all" if batch else "open",
                "--json",
                "number,state",
            ],
            cwd=repo,
            env=env,
            text=True,
        )
    )
    if batch and any(pr["state"] == "MERGED" for pr in prs):
        print("The batch release PR is already merged.", flush=True)
        return
    push_release_refs(repo, [], branch=branch, commit=commit, update_branch=not batch)

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as body_file:
        body_file.write(body)
        body_file.flush()
        existing = next((pr["number"] for pr in prs if pr["state"] == "OPEN"), None)
        command = ["edit", str(existing)] if existing else ["create", "--base", "main", "--head", branch]
        run(repo, "gh", "pr", *command, "--repo", repository, "--title", title, "--body-file", body_file.name, env=env)


def resolve_release(repo: Path, package: str, requested_version: str) -> PublishState:
    state = resolve_publish_state(repo, package, requested_version)
    automatic = requested_version.strip().lower() in ("", "auto")
    if not automatic or state.state != "complete" or state.version != state.current_version:
        return state

    # Squash merges record the version and dependency bounds without preserving the tag's commit.
    workspace = load_workspace_packages(repo)
    target = require_package(workspace, package)
    dependency_name = canonicalize_name(target.name)
    if any(
        lower_bound(dependent.dependencies[dependency_name]) != target.version
        for dependent in dependent_packages(workspace, target)
    ):
        return state
    owner = batch_owner(repo, state.release_commit)
    if owner:
        batch = recover_batch(repo, owner)
        if not batch or not all(
            pypi_release_status(release.package, Version(release.version)).complete for release in batch
        ):
            return state
    return resolve_publish_state(repo, package, str(next_patch_version(target.version)))


def new_batch_plan(repo: Path, ordered: list[str], requested_version: str) -> list[Release]:
    plan = []
    for index, package in enumerate(ordered):
        state = resolve_release(repo, package, requested_version if index == 0 else "")
        if state.state != "new":
            raise ValueError(
                f"{package} {state.version} has an existing release tag. Finish and sync that release to main "
                "before starting a new batch, or rerun the workflow that owns its tags."
            )
        plan.append(Release(package, state.version, ""))
    return plan


def batch_subject(release: Release, run_id: str) -> str:
    return f"chore: release {release.package} {release.version} (batch {run_id})"


def batch_owner(repo: Path, commit: str) -> str | None:
    subject = run_git(repo, "show", "-s", "--format=%s", commit).stdout.strip()
    match = re.search(r" \(batch ([0-9]+)\)$", subject)
    return match[1] if match else None


def recover_batch(repo: Path, run_id: str) -> list[Release]:
    marker = f"Package-Release-Batch: {run_id}"
    first = run_git(repo, "log", "--tags", "-1", "--format=%H", f"--grep=^{marker}$").stdout.strip()
    if not first:
        return []
    message = run_git(repo, "show", "-s", "--format=%B", first).stdout
    plan = json.loads(message.partition("\nRelease-Plan: ")[2])
    previous = run_git(repo, "rev-parse", f"{first}^").stdout.strip()
    releases = []
    for name, version in plan:
        release = Release(name, version, tag_commit(repo, f"{name}-v{version}") or "")
        if not release.commit:
            raise ValueError(f"Missing reserved tag {release.tag} for batch {run_id}.")
        details = run_git(repo, "show", "-s", "--format=%P%n%s", release.commit).stdout.splitlines()
        if details != [previous, batch_subject(release, run_id)]:
            raise ValueError(f"Release tag {release.tag} belongs to a different release sequence.")
        releases.append(release)
        previous = release.commit
    return releases


def stage_artifacts(repo: Path, release: Release, directory: Path) -> None:
    target = directory / release.package
    target.mkdir()
    for artifact in build_artifacts(repo, release.package, release.version):
        shutil.copy2(artifact, target)


def prepare_release(repo: Path, inputs: dict, run_id: str, directory: Path) -> None:
    directory.mkdir(parents=True)
    base = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    recursive = inputs.get("recursive") is True
    checked = [name for name, enabled in inputs.items() if name.startswith("datus-") and enabled is True]
    releases = recover_batch(repo, run_id) if recursive or len(checked) > 1 else []

    if releases:
        for release in releases:
            if not pypi_release_status(release.package, Version(release.version)).complete:
                run(repo, "git", "checkout", "--detach", release.commit)
                stage_artifacts(repo, release, directory)
    else:
        roots, ordered = select_packages(repo, inputs.get("package", ""), checked, recursive)
        version = inputs.get("version", "").strip()
        if len(roots) > 1 and version and version.lower() != "auto":
            raise ValueError("Leave version empty or use auto when publishing multiple selected packages.")

        if len(ordered) > 1:
            plan = new_batch_plan(repo, ordered, version)
            for index, release in enumerate(plan):
                prepare_metadata(repo, release.package, release.version)
                stage_artifacts(repo, release, directory)
                message = batch_subject(release, run_id)
                if index == 0:
                    planned_versions = [(item.package, item.version) for item in plan]
                    message += f"\n\nPackage-Release-Batch: {run_id}\nRelease-Plan: {json.dumps(planned_versions)}"
                commit_metadata(repo, message, always_commit=True)
                releases.append(
                    Release(release.package, release.version, run_git(repo, "rev-parse", "HEAD").stdout.strip())
                )
        else:
            state = resolve_release(repo, roots[0], version)
            release = Release(state.package, state.version, state.release_commit)
            summarize(
                f"### {state.package} {state.version}\n\n"
                f"- Current main version: `{state.current_version}`\n"
                f"- Latest PyPI version: `{state.latest_pypi_version or '<none>'}`\n"
                f"- Release tag: `{state.tag}`\n"
                f"- Initial publication state: `{state.state}`"
            )
            if state.state == "new":
                prepare_metadata(repo, release.package, release.version)
                stage_artifacts(repo, release, directory)
                commit_metadata(repo, f"chore: release {release.package} {release.version}")
                release = Release(state.package, state.version, run_git(repo, "rev-parse", "HEAD").stdout.strip())
            else:
                if batch_owner(repo, release.commit):
                    raise ValueError(f"{release.tag} is reserved for its batch. Rerun the workflow that owns it.")
                if state.state == "retry":
                    run(repo, "git", "checkout", "--detach", release.commit)
                    stage_artifacts(repo, release, directory)
            releases = [release]

        head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        if head != base and run_git(repo, "merge-base", "--is-ancestor", base, head, check=False).returncode == 0:
            run(repo, "git", "bundle", "create", str(directory / "release.bundle"), "HEAD", f"^{base}")

    (directory / "releases.json").write_text(json.dumps([asdict(release) for release in releases]), encoding="utf-8")


def publish_release(repo: Path, run_id: str, directory: Path, credentials: dict[str, str], gh_token: str) -> None:
    releases = [Release(**entry) for entry in json.loads((directory / "releases.json").read_text(encoding="utf-8"))]
    if not releases:
        raise ValueError("The release plan is empty.")
    if (bundle := directory / "release.bundle").is_file():
        run(repo, "git", "fetch", str(bundle), "HEAD")

    repo_packages = load_workspace_packages(repo)
    uploading: list[tuple[Release, list[str]]] = []
    new_tags = []
    for release in releases:
        if require_package(repo_packages, release.package).name != release.package:
            raise ValueError(f"Expected the canonical package name, found {release.package}.")
        version = parse_canonical_version(release.version)
        if not re.fullmatch(r"[0-9a-f]{40}", release.commit):
            raise ValueError(f"Invalid commit ID for {release.tag}.")
        if package_version_at_commit(repo, repo_packages[release.package].path, release.commit) != version:
            raise ValueError(f"Expected {release.tag} to declare version {version}.")
        existing = tag_commit(repo, release.tag)
        status = pypi_release_status(release.package, version)
        if existing and existing != release.commit:
            raise ValueError(f"Release tag {release.tag} belongs to a different release sequence.")
        if not existing:
            if status.exists:
                raise ValueError(f"{release.tag} exists on PyPI without its expected tag.")
            new_tags.append(release)
        if not status.complete:
            uploading.append((release, distribution_files(directory / release.package, release.package)))

    batch = len(releases) > 1
    final = releases[-1]
    branch = f"release/batch-{run_id}" if batch else f"release/{final.package}-{final.version}"
    for release in new_tags:
        run(repo, "git", "tag", "-a", release.tag, release.commit, "-m", f"Release {release.package} {release.version}")
    if new_tags:
        tag_refs = [f"refs/tags/{release.tag}" for release in new_tags]
        # Reserve every version only after the entire batch has passed validation.
        if batch:
            push_release_refs(repo, tag_refs)
        else:
            push_release_refs(repo, tag_refs, branch=branch, commit=final.commit, update_branch=True)

    lines = "\n".join(f"- `{release.package} {release.version}`" for release in releases)
    summarize(f"### Package release plan\n\n{lines}")
    for release, artifacts in uploading:
        publish_artifacts(repo, release.package, release.version, artifacts, credentials)
        summarize(f"- Verified `{release.package} {release.version}` on PyPI.")

    title = f"package release batch {run_id}" if batch else f"{final.package} {final.version} release"
    sync_release_pr(
        repo,
        branch,
        final.commit,
        f"[Tool] Record {title}",
        f"Record the versions and workspace dependency lower bounds for these PyPI releases.\n\n{lines}\n\n"
        "Release readiness, package-scoped tests where configured, and Twine checks passed.\n",
        gh_token,
        batch=batch,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "publish"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        run(REPO_ROOT, "git", "config", "user.name", "github-actions[bot]")
        run(REPO_ROOT, "git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        run_id = os.environ["GITHUB_RUN_ID"]
        directory = args.directory.resolve()
        if args.phase == "prepare":
            run(REPO_ROOT, "git", "checkout", "--detach")
            prepare_release(REPO_ROOT, json.loads(os.environ["RELEASE_INPUTS"]), run_id, directory)
        else:
            run(REPO_ROOT, "git", "fetch", "--tags", "origin")
            credentials = {
                key: os.environ.pop(key, "")
                for key in ("PYPI_USERNAME", "PYPI_PASSWORD", "PYPI_API_TOKEN", "PYPI_TOKEN")
            }
            publish_release(REPO_ROOT, run_id, directory, credentials, os.environ.pop("GH_TOKEN", ""))
    except (KeyError, RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Package release failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
