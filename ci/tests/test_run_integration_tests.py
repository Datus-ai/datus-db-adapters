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
    adapters = sorted(path.stem for path in (CI_ROOT / "integration" / "adapters").glob("*.sh"))

    for adapter in adapters:
        result = subprocess.run(
            [RUNNER, "--dry-run", adapter],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert f"=== Integration tests: {adapter} ===" in result.stdout
        assert f"package: datus-{adapter}" in result.stdout
