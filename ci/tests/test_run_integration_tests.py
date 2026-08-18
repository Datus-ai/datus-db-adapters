import re
import subprocess
import tomllib
from pathlib import Path

CI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CI_ROOT.parent
RUNNER = CI_ROOT / "run-integration-tests.sh"
GAUSSDB_DEFINITION = CI_ROOT / "integration" / "adapters" / "gaussdb.sh"


def test_gaussdb_client_ca_files_are_private_before_export() -> None:
    source = GAUSSDB_DEFINITION.read_text(encoding="utf-8")

    trusted_copy = source.index(
        'docker_compose -f "$ADAPTER_COMPOSE" cp gaussdb:/gaussdb-tls/ca.crt "$GAUSSDB_TLS_HOST_DIR/ca.crt"'
    )
    untrusted_copy = source.index(
        'docker_compose -f "$ADAPTER_COMPOSE" cp gaussdb:/gaussdb-tls/wrong-ca.crt "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"'
    )
    private_permissions = source.index('chmod 0600 "$GAUSSDB_TLS_HOST_DIR/ca.crt" "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"')
    trusted_export = source.index('export GAUSSDB_SSLROOTCERT="$GAUSSDB_TLS_HOST_DIR/ca.crt"')

    assert max(trusted_copy, untrusted_copy) < private_permissions < trusted_export


def test_runner_target_list_matches_impact_manifest() -> None:
    target_data = tomllib.loads((CI_ROOT / "integration-targets.toml").read_text(encoding="utf-8"))["targets"]
    expected = {name for name, config in target_data.items() if config["kind"] == "compose"}

    result = subprocess.run(
        [RUNNER, "--list"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    actual = {line.split("\t", 1)[0] for line in result.stdout.splitlines() if line}

    assert actual == expected


def test_each_adapter_definition_supports_dry_run() -> None:
    definitions = sorted(
        path for path in (CI_ROOT / "integration" / "adapters").glob("*.sh") if not path.stem.startswith("_")
    )

    for definition in definitions:
        adapter = definition.stem
        package_match = re.search(
            r'^ADAPTER_PACKAGE="([^"]+)"$',
            definition.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        assert package_match is not None, f"Missing ADAPTER_PACKAGE in {definition}"

        result = subprocess.run(
            [RUNNER, "--dry-run", adapter],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"=== Integration tests: {adapter} ===" in result.stdout
        assert f"package: {package_match.group(1)}" in result.stdout


def test_cleanup_only_tolerates_a_missing_adapter_definition(tmp_path: Path) -> None:
    ci_root = tmp_path / "ci"
    runner = ci_root / "run-integration-tests.sh"
    runner.parent.mkdir(parents=True)
    runner.write_bytes(RUNNER.read_bytes())
    runner.chmod(0o755)

    result = subprocess.run(
        [runner, "--cleanup-only", "postgresql"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Missing integration adapter definition" in result.stderr


def test_readiness_probes_run_as_modules_to_avoid_driver_shadowing() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert 'probe_module="ci.integration.readiness.$adapter"' in source
    assert 'python -m "$probe_module"' in source
    assert 'python "$probe"' not in source

    for probe in (CI_ROOT / "integration" / "readiness").glob("*.py"):
        if probe.stem == "_common":
            continue
        assert "from ._common import require_connection" in probe.read_text(encoding="utf-8")
