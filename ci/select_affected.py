#!/usr/bin/env python3

"""Select affected workspace tests from a git change set."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGETS_FILE = REPO_ROOT / "ci" / "integration-targets.toml"

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)(\[[^]]+\])?(.*)$")
_DOC_NAMES = frozenset({"README", "README.md", "LICENSE", "LICENSE.md", "CHANGELOG", "CHANGELOG.md"})
_GLOBAL_ALL_INTEGRATION_PATHS = frozenset(
    {
        ".github/workflows/test.yml",
        "ci/integration-targets.toml",
        "ci/select_affected.py",
    }
)
_GLOBAL_ALL_UNIT_PATHS = frozenset(
    {
        ".github/workflows/test.yml",
        "ci/run-unit-tests.sh",
        "ci/select_affected.py",
    }
)
_GLOBAL_ALL_SMOKE_PATHS = frozenset(
    {
        "ci/package_release.py",
        "ci/run_package_smoke.py",
        "ci/select_affected.py",
    }
)
_GLOBAL_COMPOSE_INTEGRATION_PATHS = frozenset(
    {
        "ci/integration/readiness/_common.py",
        "ci/run-integration-tests.sh",
    }
)
_CLOUD_WORKFLOW_TARGETS = {
    ".github/workflows/bigquery-cloud-tests.yml": "bigquery",
    ".github/workflows/hologres-cloud-tests.yml": "hologres",
    ".github/workflows/maxcompute-cloud-tests.yml": "maxcompute",
}


def canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise ValueError(f"Unable to parse dependency requirement: {requirement!r}")
    return canonicalize_name(match.group(1))


def normalized_runtime_requirement(requirement: str) -> str:
    """Ignore release-only lower-bound updates for workspace dependencies."""

    match = _REQUIREMENT_NAME.match(requirement)
    if match is None:
        raise ValueError(f"Unable to parse dependency requirement: {requirement!r}")
    name, extras, remainder = match.groups()
    canonical_name = canonicalize_name(name)
    if not canonical_name.startswith("datus-"):
        return " ".join(requirement.split())

    marker = ""
    if ";" in remainder:
        marker = ";" + remainder.split(";", 1)[1].strip()
    return f"{canonical_name}{extras or ''}{marker}"


@dataclass(frozen=True)
class WorkspacePackage:
    name: str
    path: PurePosixPath
    dependencies: frozenset[str]


@dataclass(frozen=True)
class IntegrationTarget:
    name: str
    package: str
    kind: str
    enabled: bool


@dataclass
class ImpactSelection:
    unit_packages: set[str] = field(default_factory=set)
    smoke_packages: set[str] = field(default_factory=set)
    compose_targets: set[str] = field(default_factory=set)
    cloud_targets: set[str] = field(default_factory=set)
    reasons: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add_reason(self, target: str, reason: str) -> None:
        self.reasons[target].add(reason)

    def as_json(self) -> dict[str, object]:
        return {
            "unit_packages": sorted(self.unit_packages),
            "smoke_packages": sorted(self.smoke_packages),
            "compose_targets": sorted(self.compose_targets),
            "cloud_targets": sorted(self.cloud_targets),
            "reasons": {target: sorted(reasons) for target, reasons in sorted(self.reasons.items())},
        }


def load_workspace_packages(repo_root: Path) -> dict[str, WorkspacePackage]:
    root_data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    members = root_data.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    packages: dict[str, WorkspacePackage] = {}

    for member in members:
        member_path = PurePosixPath(member)
        pyproject_path = repo_root / member_path / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        project = data.get("project", {})
        name = canonicalize_name(project["name"])
        dependencies = frozenset(requirement_name(requirement) for requirement in project.get("dependencies", []))
        packages[name] = WorkspacePackage(name=name, path=member_path, dependencies=dependencies)

    return packages


def load_integration_targets(targets_file: Path = TARGETS_FILE) -> dict[str, IntegrationTarget]:
    data = tomllib.loads(targets_file.read_text(encoding="utf-8"))
    return {
        name: IntegrationTarget(
            name=name,
            package=canonicalize_name(config["package"]),
            kind=config["kind"],
            enabled=config.get("enabled", True),
        )
        for name, config in data["targets"].items()
    }


def load_packages_without_live_targets(targets_file: Path = TARGETS_FILE) -> set[str]:
    data = tomllib.loads(targets_file.read_text(encoding="utf-8"))
    return {
        canonicalize_name(package) for package in data.get("selection", {}).get("packages_without_live_targets", [])
    }


def reverse_dependency_closure(packages: Mapping[str, WorkspacePackage], seeds: Iterable[str]) -> set[str]:
    reverse_dependencies: dict[str, set[str]] = defaultdict(set)
    for package in packages.values():
        for dependency in package.dependencies:
            if dependency in packages:
                reverse_dependencies[dependency].add(package.name)

    affected = {canonicalize_name(seed) for seed in seeds}
    queue = deque(affected)
    while queue:
        package = queue.popleft()
        for dependent in reverse_dependencies.get(package, set()):
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)
    return affected


def package_runtime_projection(text: str | None) -> object:
    if text is None:
        return None

    data = tomllib.loads(text)
    project = data.get("project", {})
    optional_dependencies = {
        name: sorted(normalized_runtime_requirement(requirement) for requirement in requirements)
        for name, requirements in project.get("optional-dependencies", {}).items()
        if name not in {"dev", "test", "tests"}
    }
    return {
        "name": canonicalize_name(project.get("name", "")),
        "dependencies": sorted(
            normalized_runtime_requirement(requirement) for requirement in project.get("dependencies", [])
        ),
        "optional_dependencies": optional_dependencies,
        "scripts": project.get("scripts", {}),
        "entry_points": project.get("entry-points", {}),
    }


def package_pyproject_runtime_changed(before: str | None, after: str | None) -> bool:
    return package_runtime_projection(before) != package_runtime_projection(after)


def is_documentation_path(relative_path: PurePosixPath) -> bool:
    return relative_path.name in _DOC_NAMES or relative_path.parts[:1] == ("docs",)


def is_unit_test_path(relative_path: PurePosixPath) -> bool:
    parts = relative_path.parts
    return parts[:2] == ("tests", "unit") or (parts[:1] == ("tests",) and "integration" not in parts)


def is_integration_path(relative_path: PurePosixPath) -> bool:
    parts = relative_path.parts
    return (
        parts[:2] == ("tests", "integration") or relative_path.name == "docker-compose.yml" or parts[:1] == ("scripts",)
    )


def package_for_path(
    path: PurePosixPath, packages: Mapping[str, WorkspacePackage]
) -> tuple[WorkspacePackage, PurePosixPath] | None:
    for package in packages.values():
        try:
            relative_path = path.relative_to(package.path)
        except ValueError:
            continue
        return package, relative_path
    return None


def select_impacts(
    repo_root: Path,
    changed_paths: Iterable[str],
    *,
    before_text: Callable[[str], str | None] | None = None,
) -> ImpactSelection:
    packages = load_workspace_packages(repo_root)
    targets = load_integration_targets(repo_root / "ci" / "integration-targets.toml")
    packages_without_live_targets = load_packages_without_live_targets(repo_root / "ci" / "integration-targets.toml")
    selection = ImpactSelection()
    runtime_seeds: set[str] = set()
    direct_integration_packages: set[str] = set()
    global_all_reasons: set[str] = set()
    global_compose_reasons: set[str] = set()
    targets_by_package = {target.package: target for target in targets.values()}
    for raw_path in sorted(set(changed_paths)):
        path = PurePosixPath(raw_path)
        path_string = path.as_posix()
        handled_global_path = False
        if path_string in _GLOBAL_ALL_INTEGRATION_PATHS:
            global_all_reasons.add(path_string)
            handled_global_path = True

        if path_string in _GLOBAL_ALL_UNIT_PATHS:
            selection.unit_packages.update(packages)
            for package in packages:
                selection.add_reason(package, path_string)
            handled_global_path = True

        if path_string in _GLOBAL_ALL_SMOKE_PATHS:
            selection.smoke_packages.update(packages)
            for package in packages:
                selection.add_reason(package, path_string)
            handled_global_path = True

        if handled_global_path:
            continue

        if path_string in _GLOBAL_COMPOSE_INTEGRATION_PATHS:
            global_compose_reasons.add(path_string)
            continue

        cloud_target_name = _CLOUD_WORKFLOW_TARGETS.get(path_string)
        if cloud_target_name is not None:
            target = targets[cloud_target_name]
            direct_integration_packages.add(target.package)
            selection.unit_packages.add(target.package)
            selection.add_reason(target.package, path_string)
            continue

        if path == PurePosixPath("uv.lock"):
            global_all_reasons.add(path_string)
            selection.unit_packages.update(packages)
            selection.smoke_packages.update(packages)
            continue

        if path == PurePosixPath("pyproject.toml"):
            selection.unit_packages.update(packages)
            selection.smoke_packages.update(packages)
            continue

        if (
            path.parts[:2] == ("ci", "integration")
            and len(path.parts) == 4
            and path.parts[2] in {"adapters", "readiness"}
            and path.stem != "_common"
        ):
            target_name = path.stem
            target = targets.get(target_name)
            if target is None:
                raise ValueError(f"Unregistered integration target definition: {path}")
            direct_integration_packages.add(target.package)
            selection.unit_packages.add(target.package)
            selection.add_reason(target.package, path_string)
            continue

        if path.parts[:2] == ("ci", "integration"):
            global_compose_reasons.add(path_string)
            continue

        package_match = package_for_path(path, packages)
        if package_match is None:
            continue

        package, relative_path = package_match
        if is_documentation_path(relative_path):
            continue

        if relative_path == PurePosixPath("tests/conftest.py") and package.name in targets_by_package:
            direct_integration_packages.add(package.name)
            selection.unit_packages.add(package.name)
            selection.add_reason(package.name, path_string)
            continue

        if is_unit_test_path(relative_path):
            selection.unit_packages.add(package.name)
            selection.add_reason(package.name, path_string)
            continue

        if is_integration_path(relative_path):
            direct_integration_packages.add(package.name)
            selection.unit_packages.add(package.name)
            selection.add_reason(package.name, path_string)
            continue

        if relative_path == PurePosixPath("pyproject.toml"):
            current_path = repo_root / path
            after = current_path.read_text(encoding="utf-8") if current_path.exists() else None
            before = before_text(path_string) if before_text is not None else None
            selection.smoke_packages.add(package.name)
            selection.add_reason(package.name, path_string)
            if before_text is None or package_pyproject_runtime_changed(before, after):
                runtime_seeds.add(package.name)
            continue

        runtime_seeds.add(package.name)
        selection.smoke_packages.add(package.name)
        selection.add_reason(package.name, path_string)

    runtime_affected = reverse_dependency_closure(packages, runtime_seeds)
    for seed in runtime_seeds:
        for affected_package in reverse_dependency_closure(packages, {seed}) - {seed}:
            selection.add_reason(affected_package, f"transitive dependency of {seed}")
    registered_target_packages = {target.package for target in targets.values()}
    unregistered_runtime_packages = runtime_seeds - registered_target_packages - packages_without_live_targets
    if unregistered_runtime_packages:
        names = ", ".join(sorted(unregistered_runtime_packages))
        raise ValueError(f"Runtime package changes have no registered integration policy: {names}")

    selection.unit_packages.update(runtime_affected)
    integration_packages = runtime_affected | direct_integration_packages

    for target in targets.values():
        if not target.enabled:
            continue
        selected_globally = bool(global_all_reasons) or (target.kind == "compose" and bool(global_compose_reasons))
        if selected_globally or target.package in integration_packages:
            if target.kind == "compose":
                selection.compose_targets.add(target.name)
            elif target.kind == "cloud":
                selection.cloud_targets.add(target.name)
            else:
                raise ValueError(f"Unknown integration target kind {target.kind!r} for {target.name}")
            for reason in global_all_reasons:
                selection.add_reason(target.package, reason)
            if target.kind == "compose":
                for reason in global_compose_reasons:
                    selection.add_reason(target.package, reason)

    return selection


def select_all(repo_root: Path) -> ImpactSelection:
    packages = load_workspace_packages(repo_root)
    targets = load_integration_targets(repo_root / "ci" / "integration-targets.toml")
    selection = ImpactSelection(
        unit_packages=set(packages),
        smoke_packages=set(packages),
    )
    for target in targets.values():
        if not target.enabled:
            continue
        if target.kind == "compose":
            selection.compose_targets.add(target.name)
        elif target.kind == "cloud":
            selection.cloud_targets.add(target.name)
        else:
            raise ValueError(f"Unknown integration target kind {target.kind!r} for {target.name}")
        selection.add_reason(target.package, "manual full selection")
    return selection


def git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def changed_files(repo_root: Path, base_ref: str) -> tuple[list[str], str]:
    merge_base = git_output(repo_root, "merge-base", base_ref, "HEAD").strip()
    if not merge_base:
        raise RuntimeError(f"Unable to resolve merge base for {base_ref!r}")

    outputs = [
        git_output(repo_root, "diff", "--no-renames", "--name-only", f"{merge_base}..HEAD"),
        git_output(repo_root, "diff", "--no-renames", "--name-only", "--cached"),
        git_output(repo_root, "diff", "--no-renames", "--name-only"),
        git_output(repo_root, "ls-files", "--others", "--exclude-standard"),
    ]
    paths = sorted({line for output in outputs for line in output.splitlines() if line})
    return paths, merge_base


def text_at_revision(repo_root: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout
    if "does not exist" in result.stderr or "exists on disk, but not in" in result.stderr:
        return None
    raise RuntimeError(f"git show {revision}:{path} failed: {result.stderr.strip()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    change_source = parser.add_mutually_exclusive_group(required=True)
    change_source.add_argument("--base", help="Git ref used as the change-selection base")
    change_source.add_argument("--all", action="store_true", help="Select every configured target")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root to inspect")
    parser.add_argument(
        "--suite",
        choices=("unit", "smoke", "integration", "all"),
        default="all",
        help="Target set to print",
    )
    parser.add_argument(
        "--kind",
        choices=("compose", "cloud", "all"),
        default="compose",
        help="Integration target kind",
    )
    parser.add_argument("--format", choices=("lines", "json", "summary"), default="lines")
    return parser


def selected_values(selection: ImpactSelection, suite: str, kind: str) -> list[str]:
    if suite == "unit":
        return sorted(selection.unit_packages)
    if suite == "smoke":
        return sorted(selection.smoke_packages)
    if suite == "integration":
        if kind == "compose":
            return sorted(selection.compose_targets)
        if kind == "cloud":
            return sorted(selection.cloud_targets)
        return sorted(selection.compose_targets | selection.cloud_targets)
    raise ValueError("--suite all requires --format json or summary")


def format_summary(selection: ImpactSelection) -> str:
    groups = (
        ("Unit packages", selection.unit_packages),
        ("Package smoke", selection.smoke_packages),
        ("Compose integration", selection.compose_targets),
        ("Cloud integration", selection.cloud_targets),
    )
    lines: list[str] = []
    for label, values in groups:
        lines.append(f"{label}: {', '.join(sorted(values)) if values else '<none>'}")
    if selection.reasons:
        lines.append("Reasons:")
        for target, reasons in sorted(selection.reasons.items()):
            lines.append(f"  {target}: {', '.join(sorted(reasons))}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        if args.all:
            selection = select_all(repo_root)
        else:
            paths, merge_base = changed_files(repo_root, args.base)
            selection = select_impacts(
                repo_root,
                paths,
                before_text=lambda path: text_at_revision(repo_root, merge_base, path),
            )
        if args.format == "json":
            print(json.dumps(selection.as_json(), sort_keys=True))
        elif args.format == "summary":
            print(format_summary(selection))
        else:
            for value in selected_values(selection, args.suite, args.kind):
                print(value)
    except (OSError, RuntimeError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"Affected-test selection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
