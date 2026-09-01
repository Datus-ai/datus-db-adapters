#!/usr/bin/env python3

"""Ephemeral GaussDB(DWS) cluster lifecycle for the cloud integration job.

A DWS cluster is billed per hour and has no self-service serverless form, so CI
creates one for the run and deletes it afterwards instead of keeping one online.
Deleting is the only operation Huawei Cloud documents as stopping billing
outright: a stopped cluster still bills its disks, and for the smallest
(single-tier cloud-disk) flavors the docs do not say whether nodes stop billing
at all.

Subcommands:

  up     create a cluster, wait for it to accept connections, emit its address
  down   delete a cluster (idempotent: an already-gone cluster is a success)
  zones  list availability zones — read-only, so also a credential smoke test
  reap   delete abandoned CI clusters past their TTL tag

Every cluster carries an owner tag and an expiry tag so ``reap`` can clean up
after a job that was cancelled before its teardown step ran.

Configuration comes from the environment (see ci/cloud/dws/README.md):

  HUAWEICLOUD_SDK_AK / HUAWEICLOUD_SDK_SK   IAM access key of the CI user
  HUAWEICLOUD_PROJECT_ID                    project ID of the target region
  HUAWEICLOUD_REGION                        e.g. cn-north-4
  DWS_CI_VPC_ID / _SUBNET_ID / _SECURITY_GROUP_ID
  DWS_CI_AVAILABILITY_ZONE                  e.g. cn-north-4a
  DWS_CI_DB_PASSWORD                        cluster admin password
  DWS_CI_FLAVOR                             default dwsx2.xlarge.m7
  DWS_CI_NUM_NODE                           default 3 (the documented minimum)
  DWS_CI_DB_NAME / _DB_PORT / _DB_USER      defaults gaussdb / 8000 / dbadmin
  DWS_CI_TTL_MINUTES                        default 180, used by `reap`
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

CLUSTER_NAME_PREFIX = "datus-ci"
OWNER_TAG_KEY = "datus-ci-owner"
OWNER_TAG_VALUE = "datus-db-adapters"
EXPIRES_TAG_KEY = "datus-ci-expires-at"

# DWS reports these while a cluster is still being built; anything else that is
# not AVAILABLE is treated as a failure so the job stops instead of hanging.
_PENDING_STATUSES = frozenset({"CREATING", "BUILDING", "PENDING", "REBOOTING", "RESTORING"})
_READY_STATUS = "AVAILABLE"

_CREATE_TIMEOUT_SECONDS = 2400  # cluster creation is documented at 10-15 minutes
_DELETE_TIMEOUT_SECONDS = 900
_POLL_INTERVAL_SECONDS = 20


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


class ClusterError(RuntimeError):
    """Raised when the cloud reports a cluster in an unusable state."""


@dataclass(frozen=True)
class ClusterSpec:
    """Everything needed to create one CI cluster."""

    name: str
    flavor: str
    num_node: int
    availability_zone: str
    vpc_id: str
    subnet_id: str
    security_group_id: str
    db_name: str
    db_user: str
    db_password: str
    db_port: int
    ttl_minutes: int


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def build_spec(name: str, *, now: datetime | None = None) -> ClusterSpec:
    """Assemble a cluster spec from the environment."""

    del now  # kept for symmetry with expiry_timestamp(); spec itself is time-free
    num_node = _int_env("DWS_CI_NUM_NODE", 3)
    if num_node < 3:
        # The API rejects fewer: cluster mode takes 3-256 nodes.
        raise ConfigError(f"DWS_CI_NUM_NODE must be at least 3, got {num_node}")
    return ClusterSpec(
        name=name,
        flavor=os.getenv("DWS_CI_FLAVOR", "dwsx2.xlarge.m7"),
        num_node=num_node,
        availability_zone=_require_env("DWS_CI_AVAILABILITY_ZONE"),
        vpc_id=_require_env("DWS_CI_VPC_ID"),
        subnet_id=_require_env("DWS_CI_SUBNET_ID"),
        security_group_id=_require_env("DWS_CI_SECURITY_GROUP_ID"),
        db_name=os.getenv("DWS_CI_DB_NAME", "gaussdb"),
        db_user=os.getenv("DWS_CI_DB_USER", "dbadmin"),
        db_password=_require_env("DWS_CI_DB_PASSWORD"),
        db_port=_int_env("DWS_CI_DB_PORT", 8000),
        ttl_minutes=_int_env("DWS_CI_TTL_MINUTES", 180),
    )


def cluster_name(run_id: str | None = None) -> str:
    """Name the cluster after the CI run so an orphan can be traced back."""

    suffix = (run_id or os.getenv("GITHUB_RUN_ID") or "local").strip()
    # DWS names allow letters, digits and hyphens; keep it short and traceable.
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in suffix)[:24]
    return f"{CLUSTER_NAME_PREFIX}-{safe}"


def expiry_timestamp(ttl_minutes: int, *, now: datetime | None = None) -> str:
    """UTC instant after which `reap` may delete the cluster."""

    moment = now or datetime.now(timezone.utc)
    return (moment + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(tag_value: str, *, now: datetime | None = None) -> bool:
    """Whether an expiry tag is in the past. Unparseable tags count as expired.

    A tag we cannot read belongs to a cluster nothing else will clean up, so
    reaping it is safer than leaking it.
    """

    moment = now or datetime.now(timezone.utc)
    try:
        deadline = datetime.strptime(tag_value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return deadline <= moment


def _tag_value(tags: Iterable[Any] | None, key: str) -> str | None:
    for tag in tags or []:
        if getattr(tag, "key", None) == key:
            return getattr(tag, "value", None)
    return None


def is_ci_cluster(cluster: Any) -> bool:
    """Only clusters this tool created are ever considered for deletion."""

    name = getattr(cluster, "name", "") or ""
    return _tag_value(getattr(cluster, "tags", None), OWNER_TAG_KEY) == OWNER_TAG_VALUE and name.startswith(
        CLUSTER_NAME_PREFIX
    )


def build_client():
    """Construct the DWS client. Imported lazily so --help needs no SDK."""

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkdws.v2 import DwsClient
    from huaweicloudsdkdws.v2.region.dws_region import DwsRegion

    credentials = BasicCredentials(
        ak=_require_env("HUAWEICLOUD_SDK_AK"),
        sk=_require_env("HUAWEICLOUD_SDK_SK"),
        project_id=_require_env("HUAWEICLOUD_PROJECT_ID"),
    )
    region = DwsRegion.value_of(_require_env("HUAWEICLOUD_REGION"))
    return DwsClient.new_builder().with_credentials(credentials).with_region(region).build()


def create_cluster(client, spec: ClusterSpec, *, now: datetime | None = None) -> str:
    """Create the cluster and return its id."""

    from huaweicloudsdkdws.v2 import (
        CreateClusterV2Request,
        Tags,
        V2CreateCluster,
        V2CreateClusterReq,
    )

    cluster = V2CreateCluster(
        name=spec.name,
        flavor=spec.flavor,
        num_node=spec.num_node,
        db_name=spec.db_user,
        db_password=spec.db_password,
        db_port=spec.db_port,
        availability_zones=[spec.availability_zone],
        vpc_id=spec.vpc_id,
        subnet_id=spec.subnet_id,
        security_group_id=spec.security_group_id,
        tags=[
            Tags(key=OWNER_TAG_KEY, value=OWNER_TAG_VALUE),
            Tags(key=EXPIRES_TAG_KEY, value=expiry_timestamp(spec.ttl_minutes, now=now)),
        ],
    )
    response = client.create_cluster_v2(CreateClusterV2Request(body=V2CreateClusterReq(cluster=cluster)))
    cluster_id = getattr(response.cluster, "id", None)
    if not cluster_id:
        raise ClusterError(f"CreateClusterV2 returned no cluster id: {response}")
    return cluster_id


def describe(client, cluster_id: str):
    from huaweicloudsdkdws.v2 import ListClusterDetailsRequest

    return client.list_cluster_details(ListClusterDetailsRequest(cluster_id=cluster_id)).cluster


def wait_available(client, cluster_id: str, *, timeout: int = _CREATE_TIMEOUT_SECONDS, sleep=time.sleep) -> Any:
    """Poll until the cluster is AVAILABLE, or raise."""

    deadline = time.monotonic() + timeout
    last_status = "unknown"
    while time.monotonic() < deadline:
        detail = describe(client, cluster_id)
        last_status = (getattr(detail, "status", "") or "").upper()
        if last_status == _READY_STATUS:
            return detail
        if last_status not in _PENDING_STATUSES:
            raise ClusterError(
                f"Cluster {cluster_id} entered status {last_status!r} "
                f"(sub_status={getattr(detail, 'sub_status', None)!r}, "
                f"task_status={getattr(detail, 'task_status', None)!r})"
            )
        print(f"cluster {cluster_id} status={last_status}; waiting", flush=True)
        sleep(_POLL_INTERVAL_SECONDS)
    raise ClusterError(f"Timed out after {timeout}s waiting for cluster {cluster_id} (last status {last_status!r})")


def connection_host(detail: Any) -> str:
    """Pick the address tests should connect to.

    Prefers the private endpoint (CI runs inside the VPC when self-hosted) and
    falls back to the bound EIP.
    """

    for endpoint in getattr(detail, "endpoints", None) or []:
        connect_info = getattr(endpoint, "connect_info", None)
        if connect_info:
            # connect_info looks like "host:port"; the port is configured separately.
            return str(connect_info).rsplit(":", 1)[0]
    public_ip = getattr(detail, "public_ip", None)
    eip = getattr(public_ip, "eip_address", None) if public_ip else None
    if eip:
        return str(eip)
    raise ClusterError("Cluster is available but exposes neither a private endpoint nor an EIP")


def delete_cluster(client, cluster_id: str) -> bool:
    """Delete a cluster. Returns False when it was already gone."""

    from huaweicloudsdkcore.exceptions.exceptions import ServiceResponseException
    from huaweicloudsdkdws.v2 import DeleteDwsClusterRequest

    try:
        client.delete_dws_cluster(DeleteDwsClusterRequest(cluster_id=cluster_id, keep_last_manual_backup=0))
    except ServiceResponseException as exc:
        if getattr(exc, "status_code", None) == 404:
            print(f"cluster {cluster_id} already gone", flush=True)
            return False
        raise
    return True


def wait_deleted(client, cluster_id: str, *, timeout: int = _DELETE_TIMEOUT_SECONDS, sleep=time.sleep) -> None:
    """Poll until the cluster is no longer listed."""

    from huaweicloudsdkcore.exceptions.exceptions import ServiceResponseException

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            describe(client, cluster_id)
        except ServiceResponseException as exc:
            if getattr(exc, "status_code", None) == 404:
                return
            raise
        print(f"cluster {cluster_id} still deleting; waiting", flush=True)
        sleep(_POLL_INTERVAL_SECONDS)
    # Deletion is asynchronous and billing stops when the API accepts it, so a
    # slow disappearance is worth a warning rather than a failed job.
    print(f"::warning::Cluster {cluster_id} still listed after {timeout}s; verify it was deleted", flush=True)


def emit_outputs(values: dict[str, str], *, github_output: str | None = None, stream=None) -> None:
    """Publish connection details to GitHub Actions (and always to stdout)."""

    target = github_output or os.getenv("GITHUB_OUTPUT")
    for key, value in values.items():
        print(f"{key}={value}", file=stream or sys.stdout)
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def cmd_up(args: argparse.Namespace) -> int:
    name = args.name or cluster_name()
    spec = build_spec(name)
    client = build_client()

    print(f"creating cluster {spec.name} ({spec.num_node} x {spec.flavor})", flush=True)
    cluster_id = create_cluster(client, spec)
    print(f"cluster id: {cluster_id}", flush=True)
    try:
        detail = wait_available(client, cluster_id)
    except BaseException:
        # A cluster that never became usable still bills, so take it down before
        # surfacing the failure.
        print("::warning::Cluster did not become available; deleting it", flush=True)
        try:
            delete_cluster(client, cluster_id)
        except Exception as cleanup_error:  # pragma: no cover - best effort
            print(f"::error::Failed to delete cluster {cluster_id}: {cleanup_error}", flush=True)
        raise

    emit_outputs(
        {
            "cluster_id": cluster_id,
            "host": connection_host(detail),
            "port": str(spec.db_port),
            "database": spec.db_name,
            "username": spec.db_user,
        }
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    cluster_id = args.cluster_id or os.getenv("DWS_CI_CLUSTER_ID", "").strip()
    if not cluster_id:
        print("::notice::No cluster id given; nothing to delete", flush=True)
        return 0

    client = build_client()
    if delete_cluster(client, cluster_id) and args.wait:
        wait_deleted(client, cluster_id)
    return 0


def cmd_zones(args: argparse.Namespace) -> int:
    """List the availability zones this account may build clusters in.

    Also the cheapest way to prove a fresh AK/SK, project ID and region are
    wired up correctly: it is read-only and provisions nothing. The API
    reference shows a placeholder ``az1`` in its sample response while its
    create-cluster sample uses ``cn-north-7c``, so print whatever the account
    actually returns rather than trusting either.
    """

    from huaweicloudsdkdws.v2 import ListAvailabilityZonesRequest

    client = build_client()
    zones = client.list_availability_zones(ListAvailabilityZonesRequest()).availability_zones or []
    for zone in zones:
        print(
            f"{getattr(zone, 'code', '?')}\tstatus={getattr(zone, 'status', '?')}\tname={getattr(zone, 'name', '?')}",
            flush=True,
        )
    print(f"{len(zones)} zone(s); use a 'code' value for DWS_CI_AVAILABILITY_ZONE", flush=True)
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    """Delete CI clusters whose TTL has passed.

    The teardown step runs with `if: always()`, but a cancelled runner or a lost
    network can still leave a cluster billing indefinitely; this is the backstop.
    """

    from huaweicloudsdkdws.v2 import ListClustersRequest

    client = build_client()
    clusters = client.list_clusters(ListClustersRequest()).clusters or []
    reaped = 0
    for cluster in clusters:
        if not is_ci_cluster(cluster):
            continue
        expires_at = _tag_value(getattr(cluster, "tags", None), EXPIRES_TAG_KEY)
        if not is_expired(expires_at or ""):
            continue
        cluster_id = getattr(cluster, "id", None)
        name = getattr(cluster, "name", "")
        if args.dry_run:
            print(f"would delete expired cluster {name} ({cluster_id}, expires_at={expires_at})", flush=True)
            continue
        print(f"deleting expired cluster {name} ({cluster_id}, expires_at={expires_at})", flush=True)
        delete_cluster(client, cluster_id)
        reaped += 1
    print(f"reaped {reaped} cluster(s)", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="create a cluster and wait for it to be available")
    up.add_argument("--name", help="cluster name (default: datus-ci-<GITHUB_RUN_ID>)")
    up.set_defaults(func=cmd_up)

    down = sub.add_parser("down", help="delete a cluster (idempotent)")
    down.add_argument("--cluster-id", help="cluster id (default: $DWS_CI_CLUSTER_ID)")
    down.add_argument("--wait", action="store_true", help="poll until the cluster disappears")
    down.set_defaults(func=cmd_down)

    zones = sub.add_parser("zones", help="list availability zones (read-only credential check)")
    zones.set_defaults(func=cmd_zones)

    reap = sub.add_parser("reap", help="delete abandoned CI clusters past their TTL")
    reap.add_argument("--dry-run", action="store_true", help="report what would be deleted")
    reap.set_defaults(func=cmd_reap)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except ClusterError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
