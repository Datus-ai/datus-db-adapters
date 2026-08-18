import subprocess
from pathlib import Path

from ci.select_affected import REPO_ROOT, load_workspace_packages

RUNNER = Path(REPO_ROOT) / "ci" / "run-unit-tests.sh"


def test_runner_target_list_matches_workspace_packages() -> None:
    result = subprocess.run(
        [RUNNER, "--list"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = {line.split("\t", 1)[0] for line in result.stdout.splitlines() if line}

    assert actual == set(load_workspace_packages(REPO_ROOT))


def test_runner_rejects_unknown_package() -> None:
    result = subprocess.run(
        [RUNNER, "--dry-run", "datus-does-not-exist"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unknown package 'datus-does-not-exist'" in result.stderr
