"""Repair incomplete restored PyPI source entries before any uv install runs."""

import argparse
import os
import re
import subprocess
from pathlib import Path


def incomplete_packages(cache: Path) -> list[str]:
    """Find source snapshots that cannot support metadata extraction or rebuilding.

    Even a retained wheel is insufficient: a later command may need to rebuild
    with different build settings. Cache snapshots therefore keep sources too.
    uv owns cleanup; callers must not delete its internal files directly.
    """
    incomplete = set()
    for registry in cache.glob("sdists-v*/pypi"):
        for package in registry.iterdir():
            if not package.is_dir() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package.name):
                continue
            for revision in package.glob("*/*"):
                if not revision.is_dir():
                    continue
                source = revision / "src"
                if not any((source / name).is_file() for name in ("pyproject.toml", "setup.py")):
                    incomplete.add(package.name)
    return sorted(incomplete)


def repair_cache(cache: Path) -> list[str]:
    packages = incomplete_packages(cache)
    for package in packages:
        print(f"Discarding incomplete uv source cache for {package}", flush=True)
        subprocess.run(["uv", "cache", "clean", "--cache-dir", str(cache), package], check=True)
    print(f"uv source-cache check: {len(packages)} package(s) invalidated", flush=True)
    return packages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=os.environ.get("UV_CACHE_DIR"))
    args = parser.parse_args()
    if args.cache_dir is None:
        parser.error("set UV_CACHE_DIR or pass --cache-dir")
    repair_cache(args.cache_dir)


if __name__ == "__main__":
    main()
