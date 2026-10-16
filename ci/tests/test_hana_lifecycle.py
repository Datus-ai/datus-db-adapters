"""Exercise the real runner with fake Docker/SDK commands at its process boundary."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("test_failure", [False, True])
def test_hana_stages_before_start_and_stops_before_state_cleanup(tmp_path, test_failure):
    for relative in ("ci/run-integration-tests.sh", "ci/integration/adapters/hana.sh"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    for relative in ("datus-hana/tests/integration", "ci/integration/readiness"):
        (tmp_path / relative).mkdir(parents=True)
    (tmp_path / "datus-hana/docker-compose.yml").write_text("services: {}")
    (tmp_path / "ci/integration/readiness/hana.py").touch()
    command = tmp_path / "fake-command"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "root = pathlib.Path(os.environ['FAKE_ROOT'])\n"
        "name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]\n"
        "with (root / 'events.jsonl').open('a') as out:\n"
        "    out.write(json.dumps([name, args]) + '\\n')\n"
        "running, staged = root / 'running', root / 'staged'\n"
        "if name == 'sudo':\n"
        "    if '--cleanup' in args:\n"
        "        assert not running.exists(), 'Must stop HANA before deleting its database'\n"
        "        staged.unlink(missing_ok=True)\n"
        "    else:\n"
        "        staged.touch()\n"
        "elif name == 'docker':\n"
        "    if 'up' in args:\n"
        "        assert staged.exists(), 'Bootstrap password must exist before image startup'\n"
        "        running.touch()\n"
        "    elif 'down' in args:\n"
        "        running.unlink(missing_ok=True)\n"
        "    elif 'inspect' in args:\n"
        "        print('healthy')\n"
        "    elif 'ps' in args and '-q' in args:\n"
        "        print('fake-hana-container')\n"
        "elif name == 'uv' and 'pytest' in args:\n"
        "    assert staged.exists() and running.exists()\n"
        "    sys.exit(int(os.environ['FAKE_TEST_FAILURE']))\n"
    )
    command.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("docker", "uv", "sudo"):
        (binaries / name).symlink_to(command)
    result = subprocess.run(
        ["bash", str(tmp_path / "ci/run-integration-tests.sh"), "hana"],
        env=os.environ
        | {
            "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
            "FAKE_ROOT": str(tmp_path),
            "FAKE_TEST_FAILURE": str(int(test_failure)),
            "RUNNER_TEMP": str(tmp_path),
            "DATUS_TEST_HANA_PASSWORD": "LifecycleTestOnly9",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == int(test_failure), result.stdout + result.stderr
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    prepare = next(i for i, (name, args) in enumerate(events) if name == "sudo" and "--cleanup" not in args)
    startup = next(i for i, (name, args) in enumerate(events) if name == "docker" and "up" in args)
    tests = next(i for i, (name, args) in enumerate(events) if name == "uv" and "pytest" in args)
    assert prepare < startup < tests
    assert not (tmp_path / "running").exists()
    assert not (tmp_path / "staged").exists()
