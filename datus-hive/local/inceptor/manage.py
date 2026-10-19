"""Local-only TDH CE lifecycle helpers; no CI registration or vendor artifacts."""

import argparse
import http.cookiejar
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REQUIRED = ("LICENSE_SERVICE", "ZOOKEEPER", "HDFS", "YARN", "QUARK")
PENDING = {"WAITING", "PREPARING", "RUNNING"}


def compose(*args: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), *args], check=True, **kwargs)


def container_id() -> str:
    value = compose("ps", "-q", "inceptor", capture_output=True, text=True).stdout.strip()
    if not value:
        raise RuntimeError("Start the local environment with docker compose up -d inceptor first")
    return value


def manager_url() -> str:
    address = compose("port", "inceptor", "8180", capture_output=True, text=True).stdout.strip()
    return "http://" + address


class Manager:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=body, method=method, headers={"Content-Type": "application/json"}
        )
        with self.opener.open(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def login(self) -> None:
        self.request(
            "POST",
            "/api/users/login",
            {
                "userName": os.getenv("INCEPTOR_MANAGER_USER", "admin"),
                "userPassword": os.getenv("INCEPTOR_MANAGER_PASSWORD", "admin"),
            },
        )


def wait_login(manager: Manager, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            manager.login()
            return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                raise RuntimeError(f"Manager login rejected: HTTP {exc.code}; check Manager credentials") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError("Manager did not become available")
        time.sleep(3)


def wait_jobs(manager: Manager, jobs: list[int], timeout: int) -> None:
    """Only SUCCESSFUL is success; never hide failed initialization behind HS2."""
    deadline = time.monotonic() + timeout
    for job_id in jobs:
        previous = None
        while True:
            job = manager.request("GET", f"/api/operations/jobs/{job_id}")
            state = job["status"]
            if state != previous:
                print(f"Manager job {job_id}: {state}", flush=True)
                previous = state
            if state == "SUCCESSFUL":
                break
            if state not in PENDING:
                raise RuntimeError(f"Manager job {job_id} ended with {state}; inspect {manager.base}/ for task details")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Manager job {job_id} exceeded {timeout}s")
            time.sleep(3)


def services_for_cluster(manager: Manager, cluster_id: int) -> tuple[list[dict], list[dict]]:
    all_services = manager.request("GET", "/api/services")
    selected = []
    for service_type in REQUIRED:
        matches = [s for s in all_services if s["type"] == service_type and s.get("clusterId") in (None, cluster_id)]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one {service_type} service for cluster {cluster_id}, found {len(matches)}")
        selected.append(matches[0])
    return all_services, selected


def local_node(manager: Manager) -> dict:
    identity = manager.request("GET", "/api/nodes/this")
    # /nodes/this omits clusterId even after association in TDH CE 2024.5.
    nodes = manager.request("GET", "/api/nodes")
    return next((node for node in nodes if node["hostName"] == identity["hostName"]), identity)


def resume_license(service: dict) -> None:
    """Manager operation submission consults License before it can start other roles."""
    sid = service["sid"]
    if not sid.isalnum():
        raise ValueError("Invalid License service SID")
    subprocess.run(
        ["docker", "exec", container_id(), "systemctl", "start", f"transwarp-license-server@{sid}.service"],
        check=True,
    )


def initialize(manager: Manager, timeout: int) -> None:
    node = local_node(manager)
    if node.get("clusterId") is None:
        clusters = manager.request("GET", "/api/clusters")
        cluster = next((c for c in clusters if c["name"] == "datus-inceptor-local"), None)
        if cluster is None:
            cluster = manager.request("POST", "/api/clusters", {"name": "datus-inceptor-local"})
        manager.request(
            "POST",
            "/api/nodes/this",
            {"clusterId": cluster["id"], "hostname": node["hostName"], "ip": node["ipAddress"]},
        )
        node = local_node(manager)
    all_services, selected = services_for_cluster(manager, node["clusterId"])
    license_service = next(s for s in selected if s["type"] == "LICENSE_SERVICE")
    if license_service["installed"]:
        # boot.sh starts Manager but not License on an existing volume. Starting the
        # vendor daemon preserves normal license enforcement and avoids waiting on a stopped daemon.
        resume_license(license_service)
    missing = [s["id"] for s in selected if not s["installed"]]
    if missing:
        # HDFS/Quark templates reference Aquila/Guardian defaults, even without their runtimes.
        manager.request("POST", "/api/services/initServiceConfigs", [s["id"] for s in all_services])
        jobs = manager.request("POST", "/api/services/operations/init", missing)
        wait_jobs(manager, jobs, timeout)
    roles = manager.request("GET", "/api/serviceRoles")
    # The role operation API groups a request under one service. Resume each
    # service in dependency order rather than mixing roles from multiple services.
    for service in selected:
        stopped = [r for r in roles if r["serviceId"] == service["id"] and r["frontendStatus"]["name"] != "RUNNING"]
        if stopped:
            job = manager.request("POST", f"/api/services/{service['id']}/operations/start", {})
            wait_jobs(manager, [job["id"]], timeout)
    print("Services initialized; run manage.py wait with the endpoint's authentication settings", flush=True)


def write_private(path: Path, content: str) -> None:
    # Create with restrictive permissions from the first write, including on shared hosts.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
        stream.write(content)
    path.chmod(0o600)


def prepare_realm() -> Path:
    state = ROOT / "state" / "kerberos"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    common = """[libdefaults]
 default_realm = DATUS.TEST
 dns_lookup_kdc = false
 dns_lookup_realm = false
 rdns = false
 dns_canonicalize_hostname = false
 udp_preference_limit = 1
[realms]
 DATUS.TEST = {
  kdc = %s
 }
"""
    write_private(state / "kdc-client.conf", common % "127.0.0.1:88")
    write_private(state / "server-krb5.conf", common % "kdc:88")
    port = os.getenv("INCEPTOR_KDC_PORT", "19188")
    write_private(state / "host-krb5.conf", common % f"127.0.0.1:{int(port)}")
    write_private(
        state / "kdc.conf",
        """[kdcdefaults]
 kdc_ports = 88
 kdc_tcp_ports = 88
[realms]
 DATUS.TEST = {
  database_name = /test/principal
  key_stash_file = /test/stash
  max_life = 10h
  max_renewable_life = 7d
 }
""",
    )
    if not (state / "master-password").exists():
        write_private(state / "master-password", secrets.token_urlsafe(48))
    return state


def kerberos(manager: Manager, enable: bool, timeout: int, restart: bool = True) -> None:
    node = local_node(manager)
    _, services = services_for_cluster(manager, node["clusterId"])
    quark = next(s for s in services if s["type"] == "QUARK")
    cid = container_id()
    if enable:
        state = prepare_realm()
        compose("--profile", "kerberos", "up", "-d", "--build", "--wait", "kdc")
        target = "/opt/transwarp/data/datus-kerberos-test"
        subprocess.run(["docker", "exec", cid, "mkdir", "-p", target], check=True)
        for source, name in [("server.keytab", "server.keytab"), ("server-krb5.conf", "krb5.conf")]:
            subprocess.run(["docker", "cp", str(state / source), f"{cid}:{target}/{name}"], check=True)
        subprocess.run(["docker", "exec", cid, "chown", "-R", "hive:", target], check=True)
        subprocess.run(["docker", "exec", cid, "chmod", "600", target + "/server.keytab"], check=True)
    with (ROOT / "kerberos" / "runtime.py").open("rb") as script:
        subprocess.run(
            ["docker", "exec", "-i", cid, "python3", "-", "enable" if enable else "restore", quark["sid"]],
            stdin=script,
            check=True,
        )
    if not restart:
        print("Original runtime restored without starting roles; run init before enabling HS2 Kerberos", flush=True)
        return
    roles = manager.request("GET", "/api/serviceRoles")
    ids = [
        r["id"]
        for r in roles
        if r["serviceId"] == quark["id"] and r["roleType"] in {"INCEPTOR_SERVER", "INCEPTOR_EXECUTOR"}
    ]
    if len(ids) != 2:
        raise RuntimeError("Expected one Inceptor Server and one Executor")
    job = manager.request("POST", "/api/serviceRoles/operations/restart", ids)
    wait_jobs(manager, [job["id"]], timeout)
    if enable:
        print("Kerberos configured; acquire a client ticket before running readiness", flush=True)
    else:
        print("Original runtime restored; run readiness with the original authentication mode", flush=True)


def wait_endpoint(timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no attempt completed"
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / "probe.py")],
                capture_output=True,
                text=True,
                timeout=min(90, max(1, deadline - time.monotonic())),
            )
            if result.returncode == 0:
                print(result.stdout.strip(), flush=True)
                return
            last_error = result.stderr[-1500:]
        except subprocess.TimeoutExpired:
            last_error = "Authenticated storage/metadata probe timed out"
        print("Waiting for authenticated HS2, executor, storage and metadata", flush=True)
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"Inceptor readiness failed after {timeout}s: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "wait", "kerberos-enable", "kerberos-disable"])
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--no-restart", action="store_true", help="kerberos-disable only: restore before cold startup")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.no_restart and args.action != "kerberos-disable":
        parser.error("--no-restart is only valid with kerberos-disable")
    if args.action == "wait":
        wait_endpoint(args.timeout)
        return
    manager = Manager(manager_url())
    wait_login(manager, min(300, args.timeout))
    if args.action == "init":
        initialize(manager, args.timeout)
    else:
        kerberos(manager, args.action == "kerberos-enable", args.timeout, restart=not args.no_restart)


if __name__ == "__main__":
    main()
