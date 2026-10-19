"""Local provisioning must fail closed and preserve installed services on restart."""

import importlib.util
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2] / "local" / "inceptor"


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def manage(monkeypatch):
    module = load_helper("manage")
    clock = iter(range(0, 10000, 3))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    module.resumed = []
    monkeypatch.setattr(module, "resume_license", lambda service: module.resumed.append(service["id"]))
    return module


def test_manager_delayed_start_and_rejected_credentials(manage):
    attempts = iter([urllib.error.URLError("starting"), None])

    class Login:
        def login(self):
            result = next(attempts)
            if result:
                raise result

    manage.wait_login(Login(), 60)
    denied = urllib.error.HTTPError("http://localhost/", 401, "Unauthorized", {}, None)
    attempts = iter([denied])
    with pytest.raises(RuntimeError, match="login rejected: HTTP 401"):
        manage.wait_login(Login(), 60)


@pytest.mark.parametrize("final_status", ["SUCCESSFUL", "FAILED", "WARNING", "CANCELLED"])
def test_initialization_jobs_wait_for_completion_and_reject_failure(manage, final_status):
    statuses = iter(["WAITING", "RUNNING", final_status])

    class Jobs:
        base = "http://localhost"

        def request(self, method, path):
            assert (method, path) == ("GET", "/api/operations/jobs/37")
            return {"status": next(statuses)}

    if final_status == "SUCCESSFUL":
        manage.wait_jobs(Jobs(), [37], 60)
    else:
        with pytest.raises(RuntimeError, match=final_status):
            manage.wait_jobs(Jobs(), [37], 60)


def test_initialization_job_timeout_is_failure(manage):
    class Jobs:
        def request(self, method, path):
            return {"status": "RUNNING"}

    with pytest.raises(TimeoutError, match="job 37 exceeded"):
        manage.wait_jobs(Jobs(), [37], 2)


@pytest.mark.parametrize("installed", [False, True])
def test_fresh_provisioning_and_existing_data_restart(manage, installed):
    calls = []
    services = [
        {"id": index + 40, "type": kind, "installed": installed, "clusterId": 7}
        for index, kind in enumerate(manage.REQUIRED)
    ]
    services.append({"id": 99, "type": "AQUILA", "installed": False, "clusterId": None})

    class Provisioning:
        def request(self, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/api/nodes/this":
                return {"hostName": "local"}
            if path == "/api/nodes":
                return [{"hostName": "local", "clusterId": 7}]
            if path == "/api/services":
                return services
            if path == "/api/services/operations/init":
                return [37]
            if path == "/api/serviceRoles":
                return [
                    {"id": sid + 100, "serviceId": sid, "frontendStatus": {"name": "RUNNING" if sid == 43 else "DOWN"}}
                    for sid in range(40, 45)
                ]
            if path.startswith("/api/services/") and path.endswith("/operations/start"):
                return {"id": 38}
            if path.startswith("/api/operations/jobs/"):
                return {"status": "SUCCESSFUL"}

    manage.initialize(Provisioning(), 60)
    posts = [c for c in calls if c[0] == "POST"]
    expected = [("POST", f"/api/services/{sid}/operations/start", {}) for sid in (40, 41, 42, 44)]
    if not installed:
        expected = [
            ("POST", "/api/services/initServiceConfigs", [40, 41, 42, 43, 44, 99]),
            ("POST", "/api/services/operations/init", [40, 41, 42, 43, 44]),
            *expected,
        ]
    assert posts == expected
    assert manage.resumed == ([40] if installed else [])


def test_readiness_retries_failed_query_then_succeeds(manage, monkeypatch):
    results = iter(
        [
            SimpleNamespace(returncode=1, stderr="executor not registered", stdout=""),
            SimpleNamespace(returncode=0, stderr="", stdout="authenticated probe passed"),
        ]
    )
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **kw: next(results))
    manage.wait_endpoint(60)
    with pytest.raises(StopIteration):
        next(results)


def test_readiness_timeout_preserves_failure_cause(manage, monkeypatch):
    monkeypatch.setattr(
        manage.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr="authentication rejected")
    )
    with pytest.raises(TimeoutError, match="authentication rejected"):
        manage.wait_endpoint(7)


@pytest.mark.parametrize("failure", [None, "write", "tables", "views", "columns"])
def test_authenticated_storage_probe_requires_known_data_and_metadata(monkeypatch, failure):
    probe = load_helper("probe")
    calls = []

    def result(rows=None, success=True):
        return SimpleNamespace(success=success, sql_return=rows, error="write failed" if not success else None)

    class Connection:
        def __init__(self, config):
            self.config = config

        def execute(self, query, result_format):
            calls.append(query["sql_query"])
            return result([{"probe": 1}] if query["sql_query"] == "SELECT 1 AS probe" else [{"id": 1}])

        def execute_ddl(self, sql):
            calls.append(sql)
            return result()

        def execute_insert(self, sql):
            calls.append(sql)
            return result(success=failure != "write")

        def get_databases(self):
            return ["datus_readiness_fixed"]

        def get_tables(self, **kwargs):
            return ["probe", "probe_view"] if failure == "tables" else ["probe"]

        def get_views(self, **kwargs):
            return [] if failure == "views" else ["probe_view"]

        def get_schema(self, **kwargs):
            return [{"name": "id", "type": "int", "comment": "" if failure == "columns" else "identifier"}]

        def close(self):
            calls.append("close")

    monkeypatch.setattr(probe, "HiveConnector", Connection)
    monkeypatch.setattr(probe.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    if failure:
        with pytest.raises(RuntimeError, match="failed|metadata"):
            probe.probe()
    else:
        probe.probe()
    assert "INSERT INTO `datus_readiness_fixed`.`probe` SELECT 1" in calls
    assert calls[-2:] == ["DROP DATABASE `datus_readiness_fixed` CASCADE", "close"]


def test_fresh_node_association_uses_discovered_cluster(manage):
    associated = False
    posts = []
    services = [{"id": i, "type": kind, "installed": True, "clusterId": 8} for i, kind in enumerate(manage.REQUIRED)]

    class NewNode:
        def request(self, method, path, payload=None):
            nonlocal associated
            if method == "POST":
                posts.append((path, payload))
                if path == "/api/clusters":
                    return {"id": 8}
                if path == "/api/nodes/this":
                    associated = True
                    return [s["id"] for s in services]
            if path == "/api/nodes/this":
                return {"hostName": "local", "ipAddress": "192.0.2.1"}
            if path == "/api/nodes":
                return [{"hostName": "local", "clusterId": 8}] if associated else []
            if path == "/api/clusters":
                return []
            if path == "/api/services":
                return services
            if path == "/api/serviceRoles":
                return []

    manage.initialize(NewNode(), 60)
    assert posts == [
        ("/api/clusters", {"name": "datus-inceptor-local"}),
        ("/api/nodes/this", {"clusterId": 8, "hostname": "local", "ip": "192.0.2.1"}),
    ]


def test_kerberos_runtime_is_readable_by_daemon_and_restores_original_files(tmp_path, monkeypatch):
    runtime = load_helper("kerberos/runtime")
    monkeypatch.setattr(runtime.socket, "gethostbyname", lambda _: "192.0.2.1")
    config_root = tmp_path / "etc"
    conf = config_root / "quark1" / "conf"
    conf.mkdir(parents=True)
    data_root = tmp_path / "data"
    data_root.mkdir()
    originals = {
        conf
        / "hive-site.xml": b"<configuration><property><name>hive.server2.authentication</name><value>NONE</value></property></configuration>",
        conf / "core-site.xml": b"<configuration/>",
        conf / "ngmr-env.sh": b"export ORIGINAL_SETTING=true\n",
        config_root / "krb5.conf": b"original realm configuration\n",
    }
    for path, content in originals.items():
        path.write_bytes(content)
        path.chmod(0o644)
    (data_root / "krb5.conf").write_text("[libdefaults]\n default_realm = DATUS.TEST\n")
    (data_root / "krb5.conf").chmod(0o600)
    (data_root / "server.keytab").write_bytes(b"test keytab")
    (data_root / "server.keytab").chmod(0o600)
    for _ in range(2):
        runtime.apply("enable", "quark1", config_root, data_root)
        assert (config_root / "krb5.conf").stat().st_mode & 0o777 == 0o644
        assert (data_root / "server.keytab").stat().st_mode & 0o777 == 0o600
        tree = runtime.ET.fromstring((conf / "hive-site.xml").read_bytes())
        values = {p.findtext("name"): p.findtext("value") for p in tree.findall("property")}
        assert values["hive.server2.authentication"] == "KERBEROS"
        assert values["hive.server2.authentication.kerberos.keytab"] == str(data_root / "server.keytab")
        assert (conf / "ngmr-env.sh").read_text().count("export EXTRA_DRIVER_OPTS=") == 1
    runtime.apply("restore", "quark1", config_root, data_root)
    assert {path: path.read_bytes() for path in originals} == originals
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in originals)
    assert (config_root / "krb5.conf").stat().st_mode & 0o777 == 0o644


def test_cold_kerberos_recovery_restores_runtime_before_starting_metastore(manage, monkeypatch):
    commands = []
    monkeypatch.setattr(manage, "container_id", lambda: "local-container")
    monkeypatch.setattr(manage.subprocess, "run", lambda command, **kw: commands.append(command))

    class ColdManager:
        def request(self, method, path):
            assert method == "GET", "Cold recovery must not submit role starts before restoring SIMPLE runtime"
            if path == "/api/nodes/this":
                return {"hostName": "local"}
            if path == "/api/nodes":
                return [{"hostName": "local", "clusterId": 7}]
            if path == "/api/services":
                return [
                    {"id": i, "type": kind, "clusterId": 7, "sid": "quark1"} for i, kind in enumerate(manage.REQUIRED)
                ]
            raise AssertionError(f"Unexpected cold-start request: {path}")

    manage.kerberos(ColdManager(), False, 60, restart=False)
    assert commands == [["docker", "exec", "-i", "local-container", "python3", "-", "restore", "quark1"]]
