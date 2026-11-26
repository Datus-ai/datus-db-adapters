"""Exercise container scripts against process/SQL boundaries without Docker."""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GAUSS_SCRIPTS = ROOT / "datus-gaussdb" / "scripts"
WRAPPER = GAUSS_SCRIPTS / "docker-entrypoint-wrapper.sh"


def run_shell(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True, timeout=10, check=False)


def executable(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    path.chmod(0o755)


@pytest.fixture
def database_env(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "pg_hba.conf").write_text("")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SCRIPT_TEST_BIN": str(fake_bin),
        "PGDATA": str(data),
        "CALLS": str(tmp_path / "calls"),
        "GAUSSDB_USER": "test_user",
        "GAUSSDB_PASSWORD": "Test_password123",
        "FAIL_STEP": "",
    }
    executable(
        fake_bin / "gsql",
        r"""
sql=$(cat)
case "$sql" in
  *"SELECT 1 FROM pg_roles"*) step=inspect ;;
  *"CREATE USER"*) step=create ;;
  *"GRANT ALL PRIVILEGES"*) step=grant ;;
  *) exit 99 ;;
esac
printf '%s\n' "$step" >> "$CALLS"
if [ "$FAIL_STEP" = "$step" ]; then
    # Like gsql, SQL errors only produce a failing process status when the
    # caller enables ON_ERROR_STOP. This catches accidentally dropping it.
    case " $* " in
      *" -v ON_ERROR_STOP=1 "*) exit 3 ;;
      *) exit 0 ;;
    esac
fi
case "$step" in
  inspect) if [ -f "$PGDATA/role" ]; then echo 1; fi ;;
  create) touch "$PGDATA/role" ;;
  grant) touch "$PGDATA/grants" ;;
esac
""",
    )
    executable(
        fake_bin / "gs_guc",
        r"""
echo config >> "$CALLS"
[ "$FAIL_STEP" != config ] || exit 3
""",
    )
    return env


@pytest.mark.parametrize("failure", ["inspect", "create", "grant", "config"])
def test_failed_provisioning_never_leaves_a_success_marker(database_env: dict[str, str], failure: str) -> None:
    marker = Path(database_env["PGDATA"]) / ".datus-test-provisioned"
    marker.touch()  # A previous success must not hide an unsuccessful repair.
    result = run_shell(
        f"bash {shlex.quote(str(GAUSS_SCRIPTS / 'provision-test-user.sh'))}",
        {
            **database_env,
            "FAIL_STEP": failure,
        },
    )
    assert result.returncode != 0
    assert not marker.exists()


def test_provisioning_repairs_a_role_created_before_a_failed_grant(database_env: dict[str, str]) -> None:
    command = f"bash {shlex.quote(str(GAUSS_SCRIPTS / 'provision-test-user.sh'))}"
    failed = run_shell(command, {**database_env, "FAIL_STEP": "grant"})
    assert failed.returncode != 0
    assert (Path(database_env["PGDATA"]) / "role").exists()
    repaired = run_shell(command, database_env)
    assert repaired.returncode == 0, repaired.stderr
    repeated = run_shell(command, database_env)
    assert repeated.returncode == 0, repeated.stderr
    data = Path(database_env["PGDATA"])
    assert (data / ".datus-test-provisioned").is_file()
    assert (data / "grants").is_file()
    calls = Path(database_env["CALLS"]).read_text().splitlines()
    assert calls.count("create") == 1
    assert calls.count("grant") == 3
    assert (data / "pg_hba.conf").read_text().splitlines() == ['host all "test_user" 0.0.0.0/0 sha256']


@pytest.mark.parametrize("failures, expected_status, expected_calls", [(0, 0, 1), (1, 0, 2), (2, 7, 2)])
def test_temporary_start_retries_once_and_propagates_failure(
    database_env: dict[str, str], failures: int, expected_status: int, expected_calls: int
) -> None:
    script = (
        f"source {shlex.quote(str(WRAPPER))}\n"
        + r"""
calls=0
gs_ctl() {
    calls=$((calls + 1))
    printf '%s\n' "$*" >> "$CALLS"
    [ "$calls" -gt "$FAILURES" ] || return 7
}
start_temporary_server gaussdb -Z single_node
"""
    )
    result = run_shell(script, {**database_env, "FAILURES": str(failures)})
    assert result.returncode == expected_status, result.stderr
    calls = Path(database_env["CALLS"]).read_text().splitlines()
    assert calls == [f"-D {database_env['PGDATA']} -w start -Z single_node"] * expected_calls


def test_failed_init_hook_cannot_fall_through_to_a_final_server(database_env: dict[str, str]) -> None:
    # Model the pinned image's sequential lifecycle while sourcing the actual
    # wrapper. The SQL failure occurs after a recovered temporary-start failure.
    fake_entrypoint = Path(database_env["PGDATA"]) / "entrypoint.sh"
    fake_entrypoint.write_text(r"""
_main() {
    docker_temp_server_start gaussdb
    bash "$PROVISION_SCRIPT"
    echo final-server >> "$CALLS"
}
""")
    script = (
        f"source {shlex.quote(str(WRAPPER))}\n"
        + r"""
source() { builtin source "$FAKE_ENTRYPOINT"; }
export PATH="$SCRIPT_TEST_BIN:$PATH"
id() { echo 1000; }
calls=0
gs_ctl() {
    calls=$((calls + 1))
    echo start >> "$CALLS"
    [ "$calls" -gt 1 ]
}
main
"""
    )
    result = run_shell(
        script,
        {
            **database_env,
            "FAKE_ENTRYPOINT": str(fake_entrypoint),
            "PROVISION_SCRIPT": str(GAUSS_SCRIPTS / "provision-test-user.sh"),
            "FAIL_STEP": "grant",
        },
    )
    assert result.returncode != 0
    assert Path(database_env["CALLS"]).read_text().splitlines() == ["start", "start", "inspect", "create", "grant"]


def healthcheck_command(adapter: str) -> str:
    compose = (ROOT / f"datus-{adapter}" / "docker-compose.yml").read_text()
    match = re.search(r"^\s+test: (\[.*\])$", compose, re.MULTILINE)
    assert match is not None
    probe = json.loads(match[1])
    if probe[0] == "CMD-SHELL":
        return probe[1].replace("$$", "$")
    return shlex.join(probe[1:])


@pytest.mark.parametrize("adapter", ["postgresql", "clickhouse", "greenplum", "mongodb"])
@pytest.mark.parametrize("final_ready", [False, True])
def test_healthcheck_rejects_init_only_listener(database_env: dict[str, str], adapter: str, final_ready: bool) -> None:
    # Match each image's temporary-listener boundary: PostgreSQL has only a
    # Unix socket; ClickHouse binds loopback. The final listener may be down
    # while either local query still succeeds.
    fake_bin = Path(database_env["PATH"].split(":")[0])
    # Greenplum accepts local SQL even before its configuration restart. Its
    # final init hook supplies the additional readiness boundary.
    executable(fake_bin / "su", "exit 0\n")
    if adapter == "greenplum" and final_ready:
        marked = run_shell(
            f"bash {shlex.quote(str(ROOT / 'datus-greenplum/scripts/mark-initialized.sh'))}",
            {**database_env, "GREENPLUM_DATA_DIRECTORY": database_env["PGDATA"]},
        )
        assert marked.returncode == 0, marked.stderr
    executable(
        fake_bin / "psql",
        r"""
case "$*" in
  *"-h "*) [ "$FINAL_READY" = 1 ] && [ "$PGPASSWORD" = "$POSTGRES_PASSWORD" ] ;;
  *) exit 0 ;;
esac
""",
    )
    executable(
        fake_bin / "clickhouse-client",
        r"""
host=localhost
while [ "$#" -gt 0 ]; do
    if [ "$1" = --host ]; then host=$2; shift; fi
    shift
done
case "$host" in
  localhost|127.0.0.1|::1) exit 0 ;;
  *) [ "$FINAL_READY" = 1 ] ;;
esac
""",
    )
    executable(
        fake_bin / "mongosh",
        r"""
host=localhost
while [ "$#" -gt 0 ]; do
    case "$1" in
        --host) host=$2; shift ;;
        --username) username=$2; shift ;;
        --password) password=$2; shift ;;
        --authenticationDatabase) auth_database=$2; shift ;;
    esac
    shift
done
case "$host" in
  localhost|127.0.0.1|::1) exit 0 ;;
  *) [ "$host" = "$HOSTNAME" ] && [ "$FINAL_READY" = 1 ] &&
     [ "$username" = "$MONGO_INITDB_ROOT_USERNAME" ] &&
     [ "$password" = "$MONGO_INITDB_ROOT_PASSWORD" ] && [ "$auth_database" = admin ] ;;
esac
""",
    )
    result = run_shell(
        healthcheck_command(adapter),
        {
            **database_env,
            "FINAL_READY": str(int(final_ready)),
            "POSTGRES_USER": "test_user",
            "POSTGRES_PASSWORD": "test_password",
            "POSTGRES_DB": "test",
            "CLICKHOUSE_USER": "default_user",
            "CLICKHOUSE_PASSWORD": "default_test",
            "CLICKHOUSE_DB": "default_test",
            "GREENPLUM_DATA_DIRECTORY": database_env["PGDATA"],
            "HOSTNAME": "test-mongodb",
            "MONGO_INITDB_ROOT_USERNAME": "test_admin",
            "MONGO_INITDB_ROOT_PASSWORD": "test_password",
        },
    )
    assert (result.returncode == 0) is final_ready


@pytest.mark.parametrize("provisioned, final_ready", [(False, False), (False, True), (True, False), (True, True)])
def test_gaussdb_health_requires_completed_provisioning_and_tls(
    database_env: dict[str, str], provisioned: bool, final_ready: bool
) -> None:
    if provisioned:
        (Path(database_env["PGDATA"]) / ".datus-test-provisioned").touch()
    fake_bin = Path(database_env["PATH"].split(":")[0])
    # The wrapper prepends the real image's binary directory. Keep commands
    # isolated even on a host with /usr/local/opengauss/bin installed.
    executable(fake_bin / "gosu", 'export PATH="$SCRIPT_TEST_BIN:$PATH"\nshift\nexec "$@"\n')
    executable(
        fake_bin / "gsql",
        r"""
# A local query works on the init server. TLS needs the final listener, and
# gsql requires -W (it does not consume PostgreSQL's PGPASSWORD variable).
case "$*" in
  *"-h 127.0.0.1 -U test_user -W Test_password123"*) : ;;
  *) exit 2 ;;
esac
if [ "${PGSSLMODE:-}" = require ]; then
    [ "$FINAL_READY" = 1 ]
fi
""",
    )
    result = run_shell(
        f"bash {shlex.quote(str(WRAPPER))} healthcheck",
        {**database_env, "FINAL_READY": str(int(final_ready))},
    )
    assert (result.returncode == 0) is (provisioned and final_ready)
