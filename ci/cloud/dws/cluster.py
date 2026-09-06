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
  flavors list node types — read-only; confirm exact names in the console
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
  DWS_CI_FLAVOR                             default dwsk2.h.xlarge.4.kc1
                                            (region-specific!)
  DWS_CI_DATASTORE_VERSION                  default 8.2.1.258 (region-specific!)
  DWS_CI_NUM_NODE                           default 3 (the documented minimum)
  DWS_CI_NUM_CN                             coordinators, default min(nodes, 3)
  DWS_CI_DB_NAME / _DB_PORT / _DB_USER      defaults gaussdb / 8000 / dbadmin
  DWS_CI_TTL_MINUTES                        default 180, used by `reap`
  DWS_CI_NAME_SUFFIX                        appended to the cluster name; set it
                                            per matrix leg so parallel versions
                                            do not collide
  DWS_CI_PUBLIC_IP                          auto_assign (default) | not_use |
                                            bind_existing (+ DWS_CI_EIP_ID)
  DWS_CI_EIP_BANDWIDTH                      Mbit/s for an auto-assigned EIP,
                                            default 5
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

# A GitHub-hosted runner reaches the cluster over the internet, so it needs an
# EIP; a self-hosted runner inside the VPC can use the private endpoint instead
# and set DWS_CI_PUBLIC_IP=not_use.
_PUBLIC_BIND_TYPES = frozenset({"auto_assign", "not_use", "bind_existing"})

# Node types are region-specific. dwsk2.xlarge (4 vCPU / 32 GB / 100 GB) is the
# smallest storage-coupled flavor offered in cn-east-3; run `flavors` to see what
# a given region actually sells, because an unavailable flavor is rejected as
# "DWS.5207 Number of CN instances is invalid", which points nowhere near it.
_DEFAULT_FLAVOR = "dwsk2.h.xlarge.4.kc1"

# Required by CreateClusterV2 despite the reference marking it optional-looking
# (its row wraps as "datastore_ver / sion"). An omitted value is rejected as
# DWS.5207 "Number of CN instances is invalid", which names the wrong field
# entirely; a wrong value at least says DWS.5003. Console → create cluster lists
# the versions a region offers.
_DEFAULT_DATASTORE_VERSION = "8.2.1.258"

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
    num_cn: int
    datastore_version: str
    availability_zone: str
    vpc_id: str
    subnet_id: str
    security_group_id: str
    db_name: str
    db_user: str
    db_password: str
    db_port: int
    ttl_minutes: int
    public_bind_type: str
    eip_bandwidth: int


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
    public_bind_type = os.getenv("DWS_CI_PUBLIC_IP", "auto_assign").strip()
    if public_bind_type not in _PUBLIC_BIND_TYPES:
        raise ConfigError(f"DWS_CI_PUBLIC_IP must be one of {sorted(_PUBLIC_BIND_TYPES)}, got {public_bind_type!r}")
    num_node = _int_env("DWS_CI_NUM_NODE", 3)
    if num_node < 3:
        # The API rejects fewer: cluster mode takes 3-256 nodes.
        raise ConfigError(f"DWS_CI_NUM_NODE must be at least 3, got {num_node}")
    # Coordinator nodes. The reference calls this optional with a default of 3,
    # but CreateClusterV2 rejects an omitted value outright (DWS.5207), so it is
    # always sent. Range is 2..min(num_node, 20).
    num_cn = _int_env("DWS_CI_NUM_CN", min(num_node, 3))
    if not 2 <= num_cn <= min(num_node, 20):
        raise ConfigError(f"DWS_CI_NUM_CN must be between 2 and {min(num_node, 20)}, got {num_cn}")
    return ClusterSpec(
        name=name,
        flavor=os.getenv("DWS_CI_FLAVOR", _DEFAULT_FLAVOR),
        num_node=num_node,
        num_cn=num_cn,
        datastore_version=os.getenv("DWS_CI_DATASTORE_VERSION", _DEFAULT_DATASTORE_VERSION),
        availability_zone=_require_env("DWS_CI_AVAILABILITY_ZONE"),
        vpc_id=_require_env("DWS_CI_VPC_ID"),
        subnet_id=_require_env("DWS_CI_SUBNET_ID"),
        security_group_id=_require_env("DWS_CI_SECURITY_GROUP_ID"),
        db_name=os.getenv("DWS_CI_DB_NAME", "gaussdb"),
        db_user=os.getenv("DWS_CI_DB_USER", "dbadmin"),
        db_password=_require_env("DWS_CI_DB_PASSWORD"),
        db_port=_int_env("DWS_CI_DB_PORT", 8000),
        ttl_minutes=_int_env("DWS_CI_TTL_MINUTES", 180),
        public_bind_type=public_bind_type,
        eip_bandwidth=_int_env("DWS_CI_EIP_BANDWIDTH", 5),
    )


def cluster_name(run_id: str | None = None, discriminator: str | None = None) -> str:
    """Name the cluster after the CI run so an orphan can be traced back.

    A matrix run creates one cluster per datastore version, so the discriminator
    (DWS_CI_NAME_SUFFIX, e.g. the version) keeps their names distinct — without
    it the second create would collide with the first.
    """

    run = (run_id or os.getenv("GITHUB_RUN_ID") or "local").strip()
    extra = (discriminator if discriminator is not None else os.getenv("DWS_CI_NAME_SUFFIX", "")).strip()
    raw = f"{run}-{extra}" if extra else run
    # DWS names allow letters, digits and hyphens; keep it short and traceable.
    safe = "".join(ch if ch.isalnum() or ch == "-" else "-" for ch in raw)[:32]
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


def build_eip_client():
    """Construct the EIP client, used only to release auto-assigned addresses."""

    from huaweicloudsdkcore.auth.credentials import BasicCredentials
    from huaweicloudsdkeip.v2 import EipClient
    from huaweicloudsdkeip.v2.region.eip_region import EipRegion

    credentials = BasicCredentials(
        ak=_require_env("HUAWEICLOUD_SDK_AK"),
        sk=_require_env("HUAWEICLOUD_SDK_SK"),
        project_id=_require_env("HUAWEICLOUD_PROJECT_ID"),
    )
    region = EipRegion.value_of(_require_env("HUAWEICLOUD_REGION"))
    return EipClient.new_builder().with_credentials(credentials).with_region(region).build()


def cluster_eip_address(detail: Any) -> str | None:
    """The EIP bound to a cluster, or None when it has no public address."""

    public_ip = getattr(detail, "public_ip", None)
    address = getattr(public_ip, "eip_address", None) if public_ip else None
    return str(address) if address else None


def release_eip(address: str, *, client=None) -> bool:
    """Delete the EIP with this exact address once it is detached.

    DWS's own `release_eip_type=RELEASE_BINDING` only unbinds: the address
    survives the cluster, unattached and still billing. Deleting it needs the
    EIP API.

    Matching is by exact address rather than "any unattached EIP" so this can
    never reach an address belonging to something else in the account.
    """

    from huaweicloudsdkeip.v2 import DeletePublicipRequest, ListPublicipsRequest

    client = client or build_eip_client()
    for publicip in client.list_publicips(ListPublicipsRequest()).publicips or []:
        if getattr(publicip, "public_ip_address", None) != address:
            continue
        if getattr(publicip, "port_id", None):
            # Still attached: either the cluster outlived the delete call or the
            # address was reassigned. Deleting it now would cut a live resource.
            print(f"::warning::EIP {address} is still attached; not releasing it", flush=True)
            return False
        client.delete_publicip(DeletePublicipRequest(publicip_id=publicip.id))
        print(f"released EIP {address}", flush=True)
        return True
    # Already gone, which is the desired end state.
    return False


def release_unbound_eips(*, client=None, dry_run: bool = False) -> int:
    """Delete every EIP that is attached to nothing. Returns how many.

    This account exists for these tests: it holds one VPC — the pre-created CI
    one — and no compute, so every EIP in it came from a cluster this tool
    built. An unattached one is therefore always a leftover, whatever produced
    it: a cancelled teardown, a cluster deleted from the console, or a run that
    predates the release step.

    That premise is what makes an unattached-means-delete sweep safe here, and
    it is the only thing that does. Should the account ever host anything else,
    this has to go back to matching by address.
    """

    from huaweicloudsdkeip.v2 import DeletePublicipRequest, ListPublicipsRequest

    client = client or build_eip_client()
    released = 0
    for publicip in client.list_publicips(ListPublicipsRequest()).publicips or []:
        if getattr(publicip, "port_id", None):
            continue
        address = getattr(publicip, "public_ip_address", None)
        if dry_run:
            print(f"would release unbound EIP {address}", flush=True)
            released += 1
            continue
        client.delete_publicip(DeletePublicipRequest(publicip_id=publicip.id))
        # Name every address deleted: this is the audit trail if the premise
        # above ever stops holding.
        print(f"released unbound EIP {address}", flush=True)
        released += 1
    return released
    return False


def create_cluster(client, spec: ClusterSpec, *, now: datetime | None = None) -> str:
    """Create the cluster and return its id."""

    from huaweicloudsdkdws.v2 import (
        CreateClusterV2Request,
        PublicIp,
        Tags,
        V2CreateCluster,
        V2CreateClusterReq,
    )

    public_ip = PublicIp(public_bind_type=spec.public_bind_type)
    if spec.public_bind_type == "auto_assign":
        public_ip.band_width = spec.eip_bandwidth
    elif spec.public_bind_type == "bind_existing":
        public_ip.eip_id = _require_env("DWS_CI_EIP_ID")

    cluster = V2CreateCluster(
        name=spec.name,
        flavor=spec.flavor,
        num_node=spec.num_node,
        num_cn=spec.num_cn,
        # V2 calls the admin account "db_name" (v1 called it user_name). The
        # database itself is not nameable here — DWS always creates "gaussdb" —
        # which is what spec.db_name carries for the connection string.
        datastore_version=spec.datastore_version,
        db_name=spec.db_user,
        db_password=spec.db_password,
        db_port=spec.db_port,
        availability_zones=[spec.availability_zone],
        vpc_id=spec.vpc_id,
        subnet_id=spec.subnet_id,
        security_group_id=spec.security_group_id,
        public_ip=public_ip,
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


def connection_host(detail: Any, *, prefer_public: bool = True) -> str:
    """Pick the address tests should connect to.

    A GitHub-hosted runner is on the internet and can only reach the EIP, while a
    self-hosted runner inside the VPC should prefer the private endpoint (faster
    and no egress charge). `prefer_public` follows DWS_CI_PUBLIC_IP.
    """

    public_ip = getattr(detail, "public_ip", None)
    eip = getattr(public_ip, "eip_address", None) if public_ip else None
    private = None
    for endpoint in getattr(detail, "endpoints", None) or []:
        connect_info = getattr(endpoint, "connect_info", None)
        if connect_info:
            # connect_info looks like "host:port"; the port is configured separately.
            private = str(connect_info).rsplit(":", 1)[0]
            break

    ordered = [eip, private] if prefer_public else [private, eip]
    for candidate in ordered:
        if candidate:
            return str(candidate)
    raise ClusterError("Cluster is available but exposes neither a private endpoint nor an EIP")


def delete_cluster(client, cluster_id: str) -> bool:
    """Delete a cluster. Returns False when it was already gone."""

    from huaweicloudsdkcore.exceptions.exceptions import ServiceResponseException
    from huaweicloudsdkdws.v2 import DeleteDwsClusterRequest

    try:
        # release_eip_type defaults to NO_RELEASE, which would leave an
        # auto-assigned EIP behind billing after the cluster is gone.
        client.delete_dws_cluster(
            DeleteDwsClusterRequest(
                cluster_id=cluster_id,
                keep_last_manual_backup=0,
                release_eip_type="RELEASE_BINDING",
            )
        )
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

    print(
        f"creating cluster {spec.name} ({spec.num_node} x {spec.flavor}, {spec.num_cn} CN, v{spec.datastore_version})",
        flush=True,
    )
    cluster_id = create_cluster(client, spec)
    # Publish the id before the long wait: if the runner is cancelled while
    # polling, the always() teardown still has something to delete. Without this
    # the cluster would bill until the reaper's TTL caught it.
    emit_outputs({"cluster_id": cluster_id})
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
            "host": connection_host(detail, prefer_public=spec.public_bind_type != "not_use"),
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
    from huaweicloudsdkcore.exceptions.exceptions import ServiceResponseException

    # Read the cluster before deleting it: the EIP address is only discoverable
    # here, and a forced delete leaks it just as easily as a checked one.
    detail = None
    try:
        detail = describe(client, cluster_id)
    except ServiceResponseException as exc:
        if getattr(exc, "status_code", None) == 404:
            print(f"::notice::Cluster {cluster_id} no longer exists", flush=True)
            return 0
        if not args.force:
            # A permission, network or API error is not evidence that the
            # cluster is gone. Swallowing it here would skip the delete and
            # leave a billing cluster behind with a success exit code.
            raise
        # --force exists for exactly this case: delete even when the cluster
        # cannot be read. The EIP stays undiscoverable, so say so rather than
        # reporting a clean teardown.
        print(
            f"::warning::Could not read cluster {cluster_id} ({exc}); "
            f"deleting anyway, but its EIP cannot be released automatically",
            flush=True,
        )

    if detail is not None and not args.force and not is_ci_cluster(detail):
        # Refuse to delete anything this tool did not create: a mistyped or
        # stale id must not take out someone's warehouse.
        raise ClusterError(
            f"Refusing to delete {cluster_id}: it lacks the {OWNER_TAG_KEY} tag "
            f"or the {CLUSTER_NAME_PREFIX} name prefix. Pass --force to override."
        )

    eip_address = cluster_eip_address(detail) if detail is not None else None

    if delete_cluster(client, cluster_id):
        # The EIP stays bound until the cluster is actually gone, so releasing
        # it means waiting even when the caller did not ask to.
        if args.wait or eip_address:
            wait_deleted(client, cluster_id)
        if eip_address:
            release_eip(eip_address)
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


def cmd_flavors(args: argparse.Namespace) -> int:
    """List node types this account can build, for DWS_CI_FLAVOR.

    Read-only. Note that the console is still authoritative for the exact
    spelling: this API answers `dwsk2.xlarge` where create wants
    `dwsk2.h.xlarge.4.kc1`, so treat the output as a shortlist to confirm
    against the console rather than as values to paste.
    """

    from huaweicloudsdkdws.v2 import ListNodeTypesRequest

    client = build_client()
    node_types = client.list_node_types(ListNodeTypesRequest()).node_types or []
    for node_type in node_types:
        detail = {d.type: d.value for d in (getattr(node_type, "detail", None) or [])}
        print(
            f"{getattr(node_type, 'spec_name', '?'):28}"
            f"vCPU={detail.get('vCPU', '?'):>4}  mem={detail.get('mem', '?'):>5}GB  "
            f"datastore={getattr(node_type, 'datastore_type', '?')}",
            flush=True,
        )
    print(f"{len(node_types)} flavor(s); confirm the exact name in the console", flush=True)
    return 0


def cmd_reap(args: argparse.Namespace) -> int:
    """Delete CI clusters whose TTL has passed.

    The teardown step runs with `if: always()`, but a cancelled runner or a lost
    network can still leave a cluster billing indefinitely; this is the backstop.
    """

    from huaweicloudsdkcore.exceptions.exceptions import ServiceResponseException
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
        # The listing carries no public_ip, so read the cluster for its address
        # before it stops being readable. It can vanish between the listing and
        # here; that is one cluster fewer to reap, not a reason to abandon the
        # remaining ones and the sweep below.
        try:
            eip_address = cluster_eip_address(describe(client, cluster_id))
        except ServiceResponseException as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            print(f"cluster {name} ({cluster_id}) is already gone", flush=True)
            continue
        if delete_cluster(client, cluster_id):
            reaped += 1
            if eip_address:
                # An abandoned cluster's EIP is exactly the kind of thing this
                # backstop exists for, and it outlives the cluster unless
                # deleted explicitly.
                wait_deleted(client, cluster_id)
                release_eip(eip_address)

    # Then everything still unattached, whoever left it: a cluster removed from
    # the console, a run that predates the release step, a teardown killed
    # between deleting the cluster and its address. Those have no owner tag to
    # match on, so nothing else would ever collect them.
    released = release_unbound_eips(dry_run=args.dry_run)
    verb = "would release" if args.dry_run else "released"
    print(f"reaped {reaped} cluster(s), {verb} {released} unbound EIP(s)", flush=True)
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
    down.add_argument("--force", action="store_true", help="skip the owner-tag check")
    down.set_defaults(func=cmd_down)

    zones = sub.add_parser("zones", help="list availability zones (read-only credential check)")
    zones.set_defaults(func=cmd_zones)

    flavors = sub.add_parser("flavors", help="list node types (read-only)")
    flavors.set_defaults(func=cmd_flavors)

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
