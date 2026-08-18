from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml"


def test_aggregate_gate_requires_selected_jobs_to_succeed() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "success|skipped" not in source
    assert 'if [ "$COMPOSE_TARGETS" = "[]" ]' in source
    assert 'require_result "compose-integration-tests" "$COMPOSE_RESULT" "success"' in source
    assert 'require_result "hologres-cloud-tests" "$HOLOGRES_RESULT" "success"' in source
    assert 'require_result "maxcompute-cloud-tests" "$MAXCOMPUTE_RESULT" "success"' in source
