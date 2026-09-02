#!/usr/bin/env python3

"""Run unit tests across the workspace with coverage and report diff coverage.

The repository is a monorepo of independent adapter packages, and running them
in one pytest invocation fails: several packages ship a module named
`tests.conftest`, which collides on import. Each package therefore runs
separately with `--cov-append`, accumulating into one `.coverage` file that is
finally rendered to `ci/coverage.xml`.

Coverage of the whole tree is reported for information only. The gate is *diff
coverage* — the share of lines this PR touched that its tests execute — because
13 of 21 packages currently sit below 80% and an overall gate would block every
PR regardless of what it changed.

Outputs (consumed by ci/post-coverage-comment.js and the workflow):

    GITHUB_OUTPUT: overall, diff, test_outcome, test_total, test_passed,
                   test_failed, test_skipped
    ci/coverage.xml             - combined coverage
    ci/diff-cover-report.md     - per-file diff coverage, rendered into the comment
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "ci"
COVERAGE_XML = OUT_DIR / "coverage.xml"
DIFF_JSON = OUT_DIR / "diff-cover.json"
DIFF_REPORT = OUT_DIR / "diff-cover-report.md"

PYTEST_TIMEOUT = 1800
DIFF_COVER_TIMEOUT = 300


def log(message: str) -> None:
    print(f"[coverage] {message}", flush=True)


def run(cmd: list[str], timeout: int, **kwargs) -> subprocess.CompletedProcess | None:
    log(f"$ {' '.join(cmd)}")
    try:
        return subprocess.run(cmd, cwd=REPO_ROOT, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        log(f"timed out after {timeout}s")
        return None


def package_specs() -> list[tuple[str, str]]:
    """(package, test path) pairs, read from the single source of truth."""
    specs = []
    runner = (REPO_ROOT / "ci" / "run-unit-tests.sh").read_text()
    block = runner.partition("PACKAGE_SPECS=(")[2].partition(")")[0]
    for line in block.splitlines():
        entry = line.strip().strip('"')
        if ":" in entry:
            package, _, test_path = entry.partition(":")
            specs.append((package, test_path))
    return specs


def importable_name(package: str) -> str:
    """datus-db-core -> datus_db_core, when that directory exists."""
    module = package.replace("-", "_")
    return module if (REPO_ROOT / package / module).is_dir() else ""


def run_tests() -> tuple[str, dict[str, int]]:
    """Run every package's unit tests, accumulating coverage. Returns outcome."""
    totals = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
    outcome = "success"

    # A stale .coverage would silently mix in a previous run's data.
    (REPO_ROOT / ".coverage").unlink(missing_ok=True)

    for package, test_path in package_specs():
        module = importable_name(package)
        if not module or not (REPO_ROOT / test_path).is_dir():
            log(f"skipping {package}: no importable module or test path")
            continue

        cmd = [
            "uv",
            "run",
            "--all-packages",
            "--with",
            "pytest",
            "--with",
            "pytest-cov",
            "--with",
            "pandas",
            "--with",
            "pyarrow",
            "pytest",
            test_path,
            "-m",
            "not integration",
            "--strict-markers",
            "--tb=short",
            "-q",
            f"--cov={module}",
            "--cov-append",
            "--cov-report=",
        ]
        proc = run(cmd, PYTEST_TIMEOUT, capture_output=True, text=True)
        if proc is None:
            outcome = "failure"
            continue

        print(proc.stdout)
        if proc.stderr.strip():
            print(proc.stderr, file=sys.stderr)
        # pytest exits 5 when a package has no unit tests at all; that is not a
        # failure of this job.
        if proc.returncode not in (0, 5):
            outcome = "failure"
        for key, pattern in (
            ("passed", r"(\d+) passed"),
            ("failed", r"(\d+) failed"),
            ("skipped", r"(\d+) skipped"),
        ):
            match = re.search(pattern, proc.stdout)
            if match:
                totals[key] += int(match.group(1))

    totals["total"] = totals["passed"] + totals["failed"] + totals["skipped"]
    return outcome, totals


def render_coverage_xml() -> float:
    """Turn the accumulated .coverage into XML; return overall line coverage."""
    OUT_DIR.mkdir(exist_ok=True)
    proc = run(
        ["uv", "run", "--with", "coverage", "coverage", "xml", "-o", str(COVERAGE_XML)],
        DIFF_COVER_TIMEOUT,
        capture_output=True,
        text=True,
    )
    if proc is None or proc.returncode != 0:
        log(f"coverage xml failed: {proc.stderr.strip() if proc else 'timeout'}")
        return 0.0
    try:
        overall = float(ET.parse(COVERAGE_XML).getroot().attrib.get("line-rate", 0)) * 100
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        log(f"cannot parse {COVERAGE_XML}: {exc}")
        return 0.0
    log(f"overall coverage: {overall:.2f}%")
    return overall


def compare_branch(base_ref: str) -> str:
    """A ref diff-cover can diff against, preferring the remote-tracking copy."""
    candidates = [f"origin/{base_ref}", base_ref, "origin/main", "main"]
    for candidate in candidates:
        proc = run(["git", "rev-parse", "--verify", "--quiet", candidate], 30, capture_output=True, text=True)
        if proc and proc.returncode == 0:
            log(f"comparing against {candidate}")
            return candidate
    log("no usable compare branch; diff coverage will be reported as 100%")
    return ""


def diff_coverage(base_ref: str) -> float:
    """Coverage of the lines this PR changed. 100% when it changed no code."""
    branch = compare_branch(base_ref)
    if not branch:
        return 100.0

    proc = run(
        [
            "uv",
            "run",
            "--with",
            "diff-cover",
            "diff-cover",
            str(COVERAGE_XML),
            f"--compare-branch={branch}",
            "--json-report",
            str(DIFF_JSON),
            "--markdown-report",
            str(DIFF_REPORT),
            "--fail-under=0",
        ],
        DIFF_COVER_TIMEOUT,
        capture_output=True,
        text=True,
    )
    if proc is None or proc.returncode != 0:
        log(f"diff-cover failed: {proc.stderr.strip() if proc else 'timeout'}")
        return 0.0

    try:
        report = json.loads(DIFF_JSON.read_text())
    except Exception as exc:  # noqa: BLE001
        log(f"cannot read {DIFF_JSON}: {exc}")
        return 0.0

    total = report.get("total_num_lines", 0)
    if not total:
        # Touching only docs or tests leaves no measurable lines; that must not
        # read as 0% and fail the gate.
        log("no measurable changed lines; diff coverage is 100%")
        return 100.0
    covered = total - report.get("total_num_violations", 0)
    percent = covered / total * 100
    log(f"diff coverage: {percent:.2f}% ({covered}/{total} changed lines)")
    return percent


def emit(outputs: dict[str, str]) -> None:
    for key, value in outputs.items():
        print(f"{key}={value}")
    target = os.getenv("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_ref", nargs="?", default="main", help="branch the PR targets")
    args = parser.parse_args(argv)

    outcome, totals = run_tests()
    overall = render_coverage_xml()
    diff = diff_coverage(args.base_ref.removeprefix("refs/heads/"))

    emit(
        {
            "overall": f"{overall:.2f}",
            "diff": f"{diff:.2f}",
            "test_outcome": outcome,
            "test_total": str(totals["total"]),
            "test_passed": str(totals["passed"]),
            "test_failed": str(totals["failed"]),
            "test_skipped": str(totals["skipped"]),
        }
    )
    # Always exit 0: the workflow decides what fails, so the comment gets posted
    # even when tests failed or coverage is short.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
