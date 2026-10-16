#!/usr/bin/env python3

"""Shared helpers for package-level adapter release automation."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class PackageInfo:
    name: str
    path: Path
    pyproject_path: Path
    version: Version
    dependencies: dict[str, Requirement]
    import_modules: tuple[str, ...]


def parse_canonical_version(raw_version: str) -> Version:
    version = Version(raw_version)
    if str(version) != raw_version:
        raise ValueError(f"Use canonical PEP 440 version {version!s} instead of {raw_version!r}")
    return version


def read_toml(path: Path) -> dict:
    with path.open("rb") as file:
        return tomllib.load(file)


def parse_dependencies(raw_dependencies: list[str]) -> dict[str, Requirement]:
    dependencies: dict[str, Requirement] = {}
    for raw_dependency in raw_dependencies:
        requirement = Requirement(raw_dependency)
        dependencies[canonicalize_name(requirement.name)] = requirement
    return dependencies


def lower_bound(requirement: Requirement) -> Version | None:
    bounds = []
    for specifier in requirement.specifier:
        if specifier.operator != ">=":
            continue
        try:
            bounds.append(Version(specifier.version))
        except InvalidVersion as exc:
            raise ValueError(f"Invalid lower bound in {requirement}: {specifier.version}") from exc
    return max(bounds) if bounds else None


def import_modules_from_pyproject(pyproject: dict, package_name: str) -> tuple[str, ...]:
    hatch_packages = (
        pyproject.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {}).get("packages")
    )
    if isinstance(hatch_packages, list) and hatch_packages:
        return tuple(str(package).replace("/", ".").strip(".") for package in hatch_packages)

    setuptools_packages = pyproject.get("tool", {}).get("setuptools", {}).get("packages")
    if isinstance(setuptools_packages, list) and setuptools_packages:
        return tuple(str(package) for package in setuptools_packages)

    setuptools_find = pyproject.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find", {})
    includes = setuptools_find.get("include") if isinstance(setuptools_find, dict) else None
    if isinstance(includes, list) and includes:
        modules = []
        for pattern in includes:
            module = str(pattern).rstrip("*").rstrip(".")
            if module:
                modules.append(module)
        if modules:
            return tuple(modules)

    return (package_name.replace("-", "_"),)


def load_workspace_packages(repo_root: Path = REPO_ROOT) -> dict[str, PackageInfo]:
    root_pyproject = read_toml(repo_root / "pyproject.toml")
    members = root_pyproject.get("tool", {}).get("uv", {}).get("workspace", {}).get("members", [])
    if not members:
        raise ValueError("No tool.uv.workspace.members found in root pyproject.toml")

    packages: dict[str, PackageInfo] = {}
    for member in members:
        package_path = repo_root / str(member)
        pyproject_path = package_path / "pyproject.toml"
        pyproject = read_toml(pyproject_path)
        project = pyproject.get("project")
        if not isinstance(project, dict):
            raise ValueError(f"{pyproject_path} is missing [project]")

        missing_fields = [field for field in ("name", "version") if field not in project]
        if missing_fields:
            joined_fields = ", ".join(missing_fields)
            raise ValueError(f"{pyproject_path} is missing required [project] field(s): {joined_fields}")

        name = str(project["name"])
        version = Version(str(project["version"]))
        normalized_name = canonicalize_name(name)
        packages[normalized_name] = PackageInfo(
            name=name,
            path=package_path,
            pyproject_path=pyproject_path,
            version=version,
            dependencies=parse_dependencies(project.get("dependencies", [])),
            import_modules=import_modules_from_pyproject(pyproject, name),
        )

    return packages


def require_package(packages: dict[str, PackageInfo], package_name: str) -> PackageInfo:
    normalized_name = canonicalize_name(package_name)
    try:
        return packages[normalized_name]
    except KeyError as exc:
        available = ", ".join(sorted(package.name for package in packages.values()))
        raise ValueError(f"Unknown package {package_name!r}. Available packages: {available}") from exc


def dependent_packages(
    packages: dict[str, PackageInfo],
    target: PackageInfo,
) -> list[PackageInfo]:
    target_name = canonicalize_name(target.name)
    return sorted(
        (
            package
            for package in packages.values()
            if canonicalize_name(package.name) != target_name and target_name in package.dependencies
        ),
        key=lambda package: package.name,
    )


def check_tag_available(repo_root: Path, package_name: str, version: Version) -> list[str]:
    tag = f"{package_name}-v{version}"
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=repo_root,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return [f"Release tag {tag} already exists"]
    return []


def update_project_version(pyproject_path: Path, version: Version, *, dry_run: bool = False) -> bool:
    lines = pyproject_path.read_text(encoding="utf-8").splitlines(keepends=True)
    in_project = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("[") and stripped.endswith("]"):
            break
        if in_project and line.startswith("version = "):
            replacement = f'version = "{version}"\n'
            if line == replacement:
                return False
            if not dry_run:
                lines[index] = replacement
                pyproject_path.write_text("".join(lines), encoding="utf-8")
            return True
    raise ValueError(f"Unable to find [project] version in {pyproject_path}")


def update_dependency_lower_bound(
    pyproject_path: Path,
    package_name: str,
    version: Version,
    *,
    dry_run: bool = False,
) -> bool:
    content = pyproject_path.read_text(encoding="utf-8")
    pattern = rf'("{re.escape(package_name)})(?:[^"]*)(")'
    replacement = rf"\1>={version}\2"
    updated, count = re.subn(pattern, replacement, content, count=1)
    if count != 1:
        raise ValueError(f"Unable to update dependency lower bound for {package_name} in {pyproject_path}")
    if updated == content:
        return False
    if not dry_run:
        pyproject_path.write_text(updated, encoding="utf-8")
    return True


def dependency_build_order(packages: dict[str, PackageInfo], target: PackageInfo) -> list[PackageInfo]:
    ordered: list[PackageInfo] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(package: PackageInfo) -> None:
        normalized_name = canonicalize_name(package.name)
        if normalized_name in visited:
            return
        if normalized_name in visiting:
            raise ValueError(f"Cycle detected while resolving workspace dependencies for {package.name}")
        visiting.add(normalized_name)
        for dependency_name in package.dependencies:
            dependency = packages.get(dependency_name)
            if dependency is not None:
                visit(dependency)
        visiting.remove(normalized_name)
        visited.add(normalized_name)
        ordered.append(package)

    visit(target)
    return ordered


def single_artifact(output_dir: Path, pattern: str, label: str, package: PackageInfo) -> Path:
    artifacts = sorted(output_dir.glob(pattern))
    if len(artifacts) != 1:
        found = ", ".join(path.name for path in artifacts) or "<none>"
        raise ValueError(
            f"Expected exactly one {label} for {package.name} in {output_dir}, found {len(artifacts)}: {found}"
        )
    return artifacts[0]


def validate_skill_resources(package: PackageInfo, wheel: Path) -> None:
    """Check packaged entrypoints and Markdown references against their source bytes."""
    resources = {
        resource.relative_to(package.path).as_posix(): resource.read_bytes()
        for skill in package.path.glob("*/skills/*/SKILL.md")
        for resource in [skill, *skill.parent.glob("references/**/*.md")]
    }
    with zipfile.ZipFile(wheel) as archive:
        packaged = {
            name
            for name in archive.namelist()
            if re.fullmatch(r"[^/]+/skills/[^/]+/(SKILL\.md|references/.+\.md)", name)
        }
        for reason, names in (("missing", resources.keys() - packaged), ("extra", packaged - resources.keys())):
            if names:
                raise ValueError(f"Wheel {wheel.name} has {reason} skill resource {sorted(names)[0]}")
        for name, content in resources.items():
            if archive.read(name) != content:
                raise ValueError(f"Wheel {wheel.name} has stale skill resource {name}")


def build_package_wheels(
    repo_root: Path,
    packages_to_build: list[PackageInfo],
    *,
    dist_root: Path | None = None,
) -> dict[str, Path]:
    if shutil.which("uv") is None:
        raise ValueError("Missing required command: uv")

    dist_root = dist_root or repo_root / "dist" / "release-check"
    wheels: dict[str, Path] = {}
    for package in packages_to_build:
        output_dir = dist_root / package.name
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["uv", "build", "--package", package.name, "--out-dir", str(output_dir)],
            cwd=repo_root,
            check=True,
        )
        single_artifact(output_dir, "*.tar.gz", "sdist", package)
        wheel = single_artifact(output_dir, "*.whl", "wheel", package)
        validate_skill_resources(package, wheel)
        wheels[canonicalize_name(package.name)] = wheel
    return wheels


def smoke_import_package(repo_root: Path, target: PackageInfo, wheels: dict[str, Path]) -> None:
    with_args: list[str] = []
    for wheel in wheels.values():
        with_args.extend(["--with", str(wheel)])

    imports = "\n".join(f"import {module}" for module in target.import_modules)
    code = f"{imports}\nprint('import smoke passed for {target.name}')\n"
    subprocess.run(
        ["uv", "run", "--no-project", "--isolated", *with_args, "python", "-c", code],
        cwd=repo_root,
        check=True,
    )
