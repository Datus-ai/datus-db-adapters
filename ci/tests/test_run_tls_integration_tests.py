import subprocess
from types import SimpleNamespace

import pytest

from ci import run_tls_integration_tests as runner


@pytest.mark.parametrize(
    "adapter,service,port",
    [
        ("mysql", "mysql", "3306"),
        ("postgresql", "postgres", "5432"),
        ("clickhouse", "clickhouse", "8443"),
        ("trino", "trino", "8443"),
    ],
)
def test_tls_runner_uses_target_readiness_and_always_cleans_up(monkeypatch, adapter, service, port):
    calls = []
    monkeypatch.setattr(
        runner.os,
        "environ",
        {"PATH": "/usr/bin:/bin", "REQUESTS_CA_BUNDLE": "/external/ca.pem", "CURL_CA_BUNDLE": "/external/ca.pem"},
    )
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

    if adapter == "trino":
        assert "REQUESTS_CA_BUNDLE" not in readiness
        assert "CURL_CA_BUNDLE" not in readiness
        assert readiness["TRINO_HTTP_SCHEME"] == "https"
        assert readiness["TRINO_VERIFY"] == "true"
        assert readiness["TRINO_REQUIRED"] == "1"
        assert readiness["TRINO_CA_CERT"].endswith("/ca.crt")
        assert readiness["TRINO_PASSWORD"] == ""
        assert readiness["TRINO_PLAIN_PORT"] == "18443"
