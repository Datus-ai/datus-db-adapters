import subprocess
from types import SimpleNamespace

import pytest

from ci import run_tls_integration_tests as runner


@pytest.mark.parametrize(
    "adapter,service,port",
    [("mysql", "mysql", "3306"), ("postgresql", "postgres", "5432"), ("clickhouse", "clickhouse", "8443")],
)
def test_tls_runner_uses_target_readiness_and_always_cleans_up(monkeypatch, adapter, service, port):
    calls = []
    monkeypatch.setattr(runner, "generate", lambda path: None)

    def run(command, **kwargs):
        calls.append((command, dict(kwargs.get("env", {}))))
        if "port" in command:
            return SimpleNamespace(stdout="127.0.0.1:18443\n")
        if f"ci.integration.readiness.{adapter}" in command:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(runner.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        runner.run(adapter)
    assert any(command[-3:] == ["port", service, port] for command, _ in calls)
    assert calls[-1][0][-3:] == ["down", "-v", "--remove-orphans"]
    readiness = next(env for command, env in calls if f"ci.integration.readiness.{adapter}" in command)
    assert readiness[f"{adapter.upper()}_PORT"] == "18443"
    if adapter == "clickhouse":
        assert readiness["CLICKHOUSE_PROTOCOL"] == "https"
        assert readiness["CLICKHOUSE_VERIFY"] == "true"
        assert readiness["CLICKHOUSE_REQUIRED"] == "1"
        assert readiness["CLICKHOUSE_CA_CERT"].endswith("/ca.crt")
