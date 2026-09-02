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


def _declared_secrets(workflow: Path) -> set[str]:
    """Secret names a reusable workflow declares under `on.workflow_call.secrets`."""
    block = workflow.read_text(encoding="utf-8").partition("    secrets:\n")[2]
    names = set()
    for line in block.splitlines():
        if line.strip() and not line.startswith("      "):
            break
        if line.startswith("      ") and not line.startswith("        ") and line.strip().endswith(":"):
            names.add(line.strip().removesuffix(":"))
    return names


def _passed_secrets(caller_source: str, job_name: str) -> set[str]:
    """Secret names test.yml maps into a reusable workflow invocation.

    Returns an empty set for `secrets: inherit`, which passes no names at all —
    that is what makes the equality assertion below reject it.
    """
    job = caller_source.partition(f"  {job_name}:\n")[2]
    block = job.partition("    secrets:\n")[2]
    names = set()
    for line in block.splitlines():
        if not line.startswith("      "):
            break
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        names.add(stripped.partition(":")[0])
    return names


def test_cloud_workflows_receive_exactly_the_secrets_they_declare() -> None:
    """Every reusable cloud workflow declares its secrets, and the caller maps
    exactly those.

    `secrets: inherit` would hand each workflow every credential in the
    repository — the Hologres job would receive the BigQuery service account key
    and vice versa. Declaring names on both sides keeps a workflow's blast radius
    to its own provider, and equality catches the half-update where a new secret
    is added to one side only.
    """
    caller_source = WORKFLOW.read_text(encoding="utf-8")
    cloud_workflows = sorted((REPO_ROOT / ".github" / "workflows").glob("*-cloud-tests.yml"))

    assert cloud_workflows, "no cloud workflows found"

    for workflow in cloud_workflows:
        job_name = workflow.stem
        declared = _declared_secrets(workflow)
        passed = _passed_secrets(caller_source, job_name)

        assert declared, f"{workflow.name} declares no workflow_call secrets"
        assert declared == passed, f"{workflow.name} declares {sorted(declared)} but test.yml passes {sorted(passed)}"
