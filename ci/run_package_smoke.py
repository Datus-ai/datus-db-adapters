#!/usr/bin/env python3

"""Build and import changed workspace packages without starting databases."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from packaging.utils import canonicalize_name

if __package__:
    from .package_release import (
        PackageInfo,
        build_package_wheels,
        dependency_build_order,
        load_workspace_packages,
        require_package,
        smoke_import_package,
    )
    from .select_affected import REPO_ROOT, changed_files, select_impacts, text_at_revision
else:
    from package_release import (
        PackageInfo,
        build_package_wheels,
        dependency_build_order,
        load_workspace_packages,
        require_package,
        smoke_import_package,
    )
    from select_affected import REPO_ROOT, changed_files, select_impacts, text_at_revision


def ordered_build_packages(packages: dict[str, PackageInfo], targets: list[PackageInfo]) -> list[PackageInfo]:
    ordered: list[PackageInfo] = []
    seen: set[str] = set()
    for target in targets:
        for package in dependency_build_order(packages, target):
            name = canonicalize_name(package.name)
            if name not in seen:
                ordered.append(package)
                seen.add(name)
    return ordered


def selected_smoke_packages(repo_root: Path, base_ref: str) -> list[str]:
    paths, merge_base = changed_files(repo_root, base_ref)
    selection = select_impacts(
        repo_root,
        paths,
        before_text=lambda path: text_at_revision(repo_root, merge_base, path),
    )
    return sorted(selection.smoke_packages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed", metavar="REF", help="Build packages affected since the merge base with REF")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root to inspect")
    parser.add_argument("packages", nargs="*", help="Explicit workspace package names")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        if args.changed and args.packages:
            raise ValueError("Use either --changed or explicit package names, not both")
        if args.changed:
            package_names = selected_smoke_packages(repo_root, args.changed)
        elif args.packages:
            package_names = sorted(set(args.packages))
        else:
            raise ValueError("Provide --changed REF or at least one package")

        if not package_names:
            print("No package build/import smoke targets detected; skipping package smoke.")
            return 0

        packages = load_workspace_packages(repo_root)
        targets = [require_package(packages, name) for name in package_names]
        build_order = ordered_build_packages(packages, targets)
        with tempfile.TemporaryDirectory(prefix="datus-package-smoke-") as temp_dir:
            wheels = build_package_wheels(repo_root, build_order, dist_root=Path(temp_dir))
            for target in targets:
                smoke_import_package(repo_root, target, wheels)

        print("Package build/import smoke passed:")
        for target in targets:
            print(f"  - {target.name}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Package build/import smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
