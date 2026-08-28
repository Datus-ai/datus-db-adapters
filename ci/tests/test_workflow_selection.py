import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-package-release.yml"


def test_publish_workflow_offers_every_workspace_package() -> None:
    source = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    options = source.partition("        options:\n")[2].partition("      version:\n")[0]
    published_packages = {
        line.removeprefix("          - ").strip() for line in options.splitlines() if line.startswith("          - ")
    }
    workspace = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    workspace_packages = set(workspace["tool"]["uv"]["workspace"]["members"])

    assert published_packages == workspace_packages


def test_aggregate_gate_requires_selected_jobs_to_succeed() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "success|skipped" not in source
    assert 'if [ "$COMPOSE_TARGETS" = "[]" ]' in source
    assert 'require_result "compose-integration-tests" "$COMPOSE_RESULT" "success"' in source
    assert 'require_result "oracle-integration-tests" "$ORACLE_RESULT" "success"' in source
    assert 'require_result "hologres-cloud-tests" "$HOLOGRES_RESULT" "success"' in source
    assert 'require_result "maxcompute-cloud-tests" "$MAXCOMPUTE_RESULT" "success"' in source
    assert 'require_result "bigquery-cloud-tests" "$BIGQUERY_RESULT" "success"' in source


def test_oracle_runs_after_the_parallel_compose_matrix() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "target for target in compose_targets if target != 'oracle'" in source
    assert "print(f\"oracle={str('oracle' in compose_targets).lower()}\")" in source
    oracle_job = source.index("  oracle-integration-tests:")
    oracle_run = source.index("run: ci/run-integration-tests.sh oracle", oracle_job)
    aggregate_job = source.index("  integration-tests:")

    assert oracle_job < oracle_run < aggregate_job
    assert "      - compose-integration-tests" in source[oracle_job:oracle_run]
