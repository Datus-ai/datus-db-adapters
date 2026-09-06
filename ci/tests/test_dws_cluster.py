"""Unit tests for the ephemeral DWS cluster lifecycle tool.

The tool talks to a billed cloud service, so every branch that decides to
create, keep or delete a cluster is exercised here against fake clients.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("dws_cluster", REPO_ROOT / "ci" / "cloud" / "dws" / "cluster.py")
cluster = importlib.util.module_from_spec(_SPEC)
sys.modules["dws_cluster"] = cluster
_SPEC.loader.exec_module(cluster)

REQUIRED_ENV = {
    "DWS_CI_AVAILABILITY_ZONE": "cn-north-4a",
    "DWS_CI_VPC_ID": "vpc-1",
    "DWS_CI_SUBNET_ID": "subnet-1",
    "DWS_CI_SECURITY_GROUP_ID": "sg-1",
    "DWS_CI_DB_PASSWORD": "Sup3r-Secret!",
}

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

# Paths that build or interpret SDK objects need the SDK; the pure decision
# logic above does not, so it still runs in a bare environment.
requires_sdk = pytest.mark.skipif(
    importlib.util.find_spec("huaweicloudsdkdws") is None,
    reason="huaweicloudsdkdws is not installed",
)

# Releasing an EIP is a separate service client. CI installs it alongside the
# DWS SDK (see .github/workflows/test.yml); without it these skip rather than
# error, the same bargain requires_sdk makes.
requires_eip_sdk = pytest.mark.skipif(
    importlib.util.find_spec("huaweicloudsdkeip") is None,
    reason="huaweicloudsdkeip is not installed",
)


@pytest.fixture
def env(monkeypatch):
    """Only the tool's own variables are set; the rest of the env is cleared."""
    for name in list(REQUIRED_ENV) + [
        "DWS_CI_NUM_NODE",
        "DWS_CI_FLAVOR",
        "DWS_CI_DB_NAME",
        "DWS_CI_DB_USER",
        "DWS_CI_DB_PORT",
        "DWS_CI_TTL_MINUTES",
        "DWS_CI_NAME_SUFFIX",
        "DWS_CI_NUM_CN",
        "DWS_CI_DATASTORE_VERSION",
        "DWS_CI_PUBLIC_IP",
        "DWS_CI_EIP_BANDWIDTH",
        "DWS_CI_CLUSTER_ID",
        "GITHUB_RUN_ID",
        "GITHUB_OUTPUT",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


class FakeClient:
    """Records calls and replays scripted cluster states."""

    def __init__(self, details=(), clusters=(), create_id="cluster-1"):
        self._details = list(details)
        self._clusters = list(clusters)
        self._create_id = create_id
        self.created = []
        self.deleted = []

    def create_cluster_v2(self, request):
        self.created.append(request)
        return SimpleNamespace(cluster=SimpleNamespace(id=self._create_id))

    def list_cluster_details(self, request):
        if not self._details:
            raise AssertionError("no more scripted cluster states")
        return SimpleNamespace(cluster=self._details.pop(0))

    def list_clusters(self, request):
        return SimpleNamespace(clusters=self._clusters)

    def delete_dws_cluster(self, request):
        self.deleted.append(request.cluster_id)
        return SimpleNamespace()


def detail(status, **kwargs):
    return SimpleNamespace(status=status, sub_status=None, task_status=None, **kwargs)


def tag(key, value):
    return SimpleNamespace(key=key, value=value)


# ==================== spec assembly ====================


def test_build_spec_reads_environment_with_documented_defaults(env):
    spec = cluster.build_spec("datus-ci-42")

    assert spec.name == "datus-ci-42"
    assert spec.flavor == "dwsk2.h.xlarge.4.kc1"
    assert spec.num_node == 3
    assert spec.db_name == "gaussdb"
    assert spec.db_user == "dbadmin"
    assert spec.db_port == 8000
    assert spec.ttl_minutes == 180
    assert spec.vpc_id == "vpc-1"


def test_build_spec_rejects_fewer_than_three_nodes(env):
    """The API rejects it too — fail before spending minutes on a create call."""
    env.setenv("DWS_CI_NUM_NODE", "1")

    with pytest.raises(cluster.ConfigError, match="at least 3"):
        cluster.build_spec("datus-ci-42")


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENV))
def test_build_spec_requires_every_infrastructure_id(env, missing):
    env.delenv(missing)

    with pytest.raises(cluster.ConfigError, match=missing):
        cluster.build_spec("datus-ci-42")


def test_build_spec_rejects_non_integer_node_count(env):
    env.setenv("DWS_CI_NUM_NODE", "three")

    with pytest.raises(cluster.ConfigError, match="must be an integer"):
        cluster.build_spec("datus-ci-42")


# ==================== naming and TTL ====================


def test_cluster_name_traces_back_to_the_run(env):
    env.setenv("GITHUB_RUN_ID", "1234567890")
    assert cluster.cluster_name() == "datus-ci-1234567890"


def test_cluster_name_sanitizes_and_truncates():
    name = cluster.cluster_name("feature/../weird name" + "x" * 40)
    assert name.startswith("datus-ci-")
    suffix = name[len("datus-ci-") :]
    assert len(suffix) <= 32
    assert all(ch.isalnum() or ch == "-" for ch in suffix)


def test_expiry_timestamp_is_ttl_minutes_ahead():
    assert cluster.expiry_timestamp(90, now=NOW) == "2026-09-01T13:30:00Z"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-09-01T11:59:59Z", True),
        ("2026-09-01T12:00:00Z", True),
        ("2026-09-01T12:00:01Z", False),
        ("not-a-timestamp", True),
        ("", True),
    ],
)
def test_is_expired_treats_unreadable_tags_as_expired(value, expected):
    """An unreadable tag means nothing else will clean that cluster up."""
    assert cluster.is_expired(value, now=NOW) is expected


# ==================== ownership guard ====================


def test_is_ci_cluster_requires_both_owner_tag_and_name_prefix():
    owned = SimpleNamespace(name="datus-ci-1", tags=[tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE)])
    assert cluster.is_ci_cluster(owned) is True


@pytest.mark.parametrize(
    "candidate",
    [
        SimpleNamespace(name="production-warehouse", tags=[tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE)]),
        SimpleNamespace(name="datus-ci-1", tags=[tag(cluster.OWNER_TAG_KEY, "someone-else")]),
        SimpleNamespace(name="datus-ci-1", tags=[]),
        SimpleNamespace(name="datus-ci-1", tags=None),
    ],
)
def test_is_ci_cluster_refuses_anything_not_provably_ours(candidate):
    assert cluster.is_ci_cluster(candidate) is False


# ==================== readiness polling ====================


@requires_sdk
def test_wait_available_returns_the_ready_cluster():
    client = FakeClient(details=[detail("CREATING"), detail("CREATING"), detail("AVAILABLE", id="cluster-1")])
    slept = []

    result = cluster.wait_available(client, "cluster-1", sleep=slept.append)

    assert result.status == "AVAILABLE"
    assert slept == [cluster._POLL_INTERVAL_SECONDS] * 2


@requires_sdk
def test_wait_available_fails_fast_on_an_unexpected_status():
    """A FAILED cluster must not be waited on until timeout — it bills meanwhile."""
    client = FakeClient(details=[detail("CREATE_FAILED")])

    with pytest.raises(cluster.ClusterError, match="CREATE_FAILED"):
        cluster.wait_available(client, "cluster-1", sleep=lambda _: None)


@requires_sdk
def test_wait_available_times_out():
    client = FakeClient(details=[detail("CREATING")])

    with pytest.raises(cluster.ClusterError, match="Timed out"):
        cluster.wait_available(client, "cluster-1", timeout=0, sleep=lambda _: None)


# ==================== connection details ====================


def test_connection_host_uses_the_eip_for_an_internet_runner():
    """A GitHub-hosted runner cannot reach the VPC private address."""
    ready = detail(
        "AVAILABLE",
        endpoints=[SimpleNamespace(connect_info="10.0.0.5:8000", jdbc_url="jdbc:postgresql://10.0.0.5:8000/gaussdb")],
        public_ip=SimpleNamespace(eip_address="1.2.3.4"),
    )
    assert cluster.connection_host(ready, prefer_public=True) == "1.2.3.4"


def test_connection_host_uses_the_private_endpoint_inside_the_vpc():
    """A self-hosted runner in the VPC skips the EIP: faster and no egress fee."""
    ready = detail(
        "AVAILABLE",
        endpoints=[SimpleNamespace(connect_info="10.0.0.5:8000", jdbc_url="jdbc:postgresql://10.0.0.5:8000/gaussdb")],
        public_ip=SimpleNamespace(eip_address="1.2.3.4"),
    )
    assert cluster.connection_host(ready, prefer_public=False) == "10.0.0.5"


def test_connection_host_falls_back_to_whichever_address_exists():
    ready = detail("AVAILABLE", endpoints=[], public_ip=SimpleNamespace(eip_address="1.2.3.4"))
    assert cluster.connection_host(ready, prefer_public=False) == "1.2.3.4"


def test_connection_host_raises_when_the_cluster_is_unreachable():
    ready = detail("AVAILABLE", endpoints=None, public_ip=None)
    with pytest.raises(cluster.ClusterError, match="neither"):
        cluster.connection_host(ready)


# ==================== outputs ====================


def test_emit_outputs_appends_to_the_github_output_file(tmp_path, capsys):
    target = tmp_path / "outputs.txt"

    cluster.emit_outputs({"host": "10.0.0.5", "port": "8000"}, github_output=str(target))

    assert target.read_text().splitlines() == ["host=10.0.0.5", "port=8000"]
    assert "host=10.0.0.5" in capsys.readouterr().out


# ==================== reaping ====================


@requires_sdk
def test_reap_deletes_only_expired_ci_clusters(env, monkeypatch):
    expired = SimpleNamespace(
        id="expired-1",
        name="datus-ci-old",
        tags=[
            tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE),
            tag(cluster.EXPIRES_TAG_KEY, (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ],
    )
    live = SimpleNamespace(
        id="live-1",
        name="datus-ci-current",
        tags=[
            tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE),
            tag(
                cluster.EXPIRES_TAG_KEY,
                (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        ],
    )
    foreign = SimpleNamespace(id="prod-1", name="production-warehouse", tags=[])
    # reap reads each doomed cluster for its EIP address; only the expired one
    # gets that far, so one scripted state is enough.
    client = FakeClient(clusters=[expired, live, foreign], details=[detail("AVAILABLE", public_ip=None)])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "build_eip_client", lambda: FakeEipClient())
    monkeypatch.setattr(cluster, "delete_cluster", lambda c, cid: c.delete_dws_cluster(SimpleNamespace(cluster_id=cid)))

    exit_code = cluster.main(["reap"])

    assert exit_code == 0
    assert client.deleted == ["expired-1"]


@requires_sdk
def test_reap_dry_run_deletes_nothing(env, monkeypatch):
    expired = SimpleNamespace(
        id="expired-1",
        name="datus-ci-old",
        tags=[
            tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE),
            tag(cluster.EXPIRES_TAG_KEY, "2020-01-01T00:00:00Z"),
        ],
    )
    client = FakeClient(clusters=[expired])
    eip_client = FakeEipClient([publicip("1.2.3.4", ip_id="eip-a")])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "build_eip_client", lambda: eip_client)

    assert cluster.main(["reap", "--dry-run"]) == 0
    assert client.deleted == []
    # The unbound sweep is part of reap, so --dry-run has to hold it back too.
    assert eip_client.deleted == []


# ==================== command wiring ====================


def test_down_without_a_cluster_id_is_a_no_op(env, monkeypatch):
    """The teardown step runs with `if: always()`, including before `up` ran."""
    monkeypatch.setattr(cluster, "build_client", lambda: pytest.fail("must not reach the cloud"))

    assert cluster.main(["down"]) == 0


@requires_sdk
def test_up_deletes_the_cluster_when_it_never_becomes_available(env, monkeypatch):
    """A cluster stuck in CREATE_FAILED still bills, so `up` cleans up before failing."""
    client = FakeClient(details=[detail("CREATE_FAILED")])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "create_cluster", lambda c, spec: "cluster-1")
    deleted = []
    monkeypatch.setattr(cluster, "delete_cluster", lambda c, cid: deleted.append(cid))

    exit_code = cluster.main(["up"])

    assert exit_code == 1
    assert deleted == ["cluster-1"]


def test_config_errors_exit_with_a_distinct_code(env):
    env.delenv("DWS_CI_VPC_ID")

    assert cluster.main(["up"]) == 2


# ==================== SDK model wiring ====================


@requires_sdk
def test_create_cluster_builds_a_tagged_request(env):
    """The owner/expiry tags are what makes `reap` safe — pin them on the wire."""
    client = FakeClient()
    spec = cluster.build_spec("datus-ci-42")

    cluster_id = cluster.create_cluster(client, spec, now=NOW)

    assert cluster_id == "cluster-1"
    sent = client.created[0].body.cluster
    assert sent.name == "datus-ci-42"
    assert sent.num_node == 3
    assert sent.availability_zones == ["cn-north-4a"]
    assert {t.key: t.value for t in sent.tags} == {
        cluster.OWNER_TAG_KEY: cluster.OWNER_TAG_VALUE,
        cluster.EXPIRES_TAG_KEY: "2026-09-01T15:00:00Z",
    }


@requires_sdk
def test_delete_cluster_treats_a_missing_cluster_as_success(env):
    from huaweicloudsdkcore.exceptions.exceptions import SdkError, ServiceResponseException

    class Missing(FakeClient):
        def delete_dws_cluster(self, request):
            raise ServiceResponseException(404, SdkError(error_msg="cluster not found"))

    assert cluster.delete_cluster(Missing(), "gone") is False


@requires_sdk
def test_delete_cluster_propagates_other_failures(env):
    from huaweicloudsdkcore.exceptions.exceptions import SdkError, ServiceResponseException

    class Broken(FakeClient):
        def delete_dws_cluster(self, request):
            raise ServiceResponseException(500, SdkError(error_msg="internal error"))

    with pytest.raises(ServiceResponseException):
        cluster.delete_cluster(Broken(), "cluster-1")


@requires_sdk
def test_zones_lists_codes_for_the_availability_zone_setting(env, monkeypatch, capsys):
    """`zones` is the read-only credential check; it must print the code column."""

    class Zones(FakeClient):
        def list_availability_zones(self, request):
            return SimpleNamespace(
                availability_zones=[
                    SimpleNamespace(code="cn-north-4a", name="AZ1", status="available"),
                    SimpleNamespace(code="cn-north-4b", name="AZ2", status="available"),
                ],
                count=2,
            )

    monkeypatch.setattr(cluster, "build_client", lambda: Zones())

    assert cluster.main(["zones"]) == 0

    out = capsys.readouterr().out
    assert "cn-north-4a" in out
    assert "status=available" in out
    assert "2 zone(s)" in out


@requires_sdk
def test_create_cluster_auto_assigns_an_eip_by_default(env):
    """Without an EIP a GitHub-hosted runner has no route to the cluster."""
    client = FakeClient()
    cluster.create_cluster(client, cluster.build_spec("datus-ci-42"), now=NOW)

    public_ip = client.created[0].body.cluster.public_ip
    assert public_ip.public_bind_type == "auto_assign"
    assert public_ip.band_width == 5


@requires_sdk
def test_create_cluster_can_skip_the_eip_for_an_in_vpc_runner(env):
    env.setenv("DWS_CI_PUBLIC_IP", "not_use")

    client = FakeClient()
    cluster.create_cluster(client, cluster.build_spec("datus-ci-42"), now=NOW)

    assert client.created[0].body.cluster.public_ip.public_bind_type == "not_use"
    # The SDK only materializes the attribute when it is set, so absence and
    # None both mean "no bandwidth requested".
    assert getattr(client.created[0].body.cluster.public_ip, "band_width", None) is None


def test_build_spec_rejects_an_unknown_public_ip_mode(env):
    env.setenv("DWS_CI_PUBLIC_IP", "sometimes")

    with pytest.raises(cluster.ConfigError, match="DWS_CI_PUBLIC_IP"):
        cluster.build_spec("datus-ci-42")


@requires_sdk
def test_delete_cluster_releases_the_bound_eip(env):
    """release_eip_type defaults to NO_RELEASE — an auto-assigned EIP would
    outlive the cluster and keep billing."""
    captured = []

    class Recording(FakeClient):
        def delete_dws_cluster(self, request):
            captured.append(request)
            return SimpleNamespace()

    cluster.delete_cluster(Recording(), "cluster-1")

    assert captured[0].release_eip_type == "RELEASE_BINDING"


def test_build_spec_defaults_coordinators_within_the_node_count(env):
    """CreateClusterV2 rejects an omitted num_cn (DWS.5207) despite the
    reference calling it optional, so a value is always derived."""
    assert cluster.build_spec("datus-ci-42").num_cn == 3

    env.setenv("DWS_CI_NUM_NODE", "6")
    assert cluster.build_spec("datus-ci-42").num_cn == 3


@pytest.mark.parametrize("value", ["1", "4"])
def test_build_spec_rejects_out_of_range_coordinator_counts(env, value):
    env.setenv("DWS_CI_NUM_CN", value)

    with pytest.raises(cluster.ConfigError, match="DWS_CI_NUM_CN"):
        cluster.build_spec("datus-ci-42")


@requires_sdk
def test_create_cluster_sends_the_coordinator_count(env):
    client = FakeClient()
    cluster.create_cluster(client, cluster.build_spec("datus-ci-42"), now=NOW)

    assert client.created[0].body.cluster.num_cn == 3


def test_cluster_name_distinguishes_matrix_legs(env):
    """One cluster per datastore version in a matrix run — the names must differ."""
    env.setenv("GITHUB_RUN_ID", "42")

    env.setenv("DWS_CI_NAME_SUFFIX", "9.1.0.227")
    first = cluster.cluster_name()
    env.setenv("DWS_CI_NAME_SUFFIX", "8.2.1.258")
    second = cluster.cluster_name()

    assert first == "datus-ci-42-9-1-0-227"
    assert second == "datus-ci-42-8-2-1-258"
    assert first != second


def test_cluster_name_without_a_suffix_is_unchanged(env):
    env.setenv("GITHUB_RUN_ID", "42")
    assert cluster.cluster_name() == "datus-ci-42"


@requires_sdk
def test_up_publishes_the_cluster_id_before_waiting(env, monkeypatch, tmp_path):
    """A cancelled runner must still leave the always() teardown an id to use."""
    target = tmp_path / "outputs.txt"
    env.setenv("GITHUB_OUTPUT", str(target))
    client = FakeClient(details=[detail("CREATING")])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "create_cluster", lambda c, spec: "cluster-1")
    monkeypatch.setattr(cluster, "delete_cluster", lambda c, cid: True)
    monkeypatch.setattr(cluster, "wait_available", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        cluster.main(["up"])

    assert "cluster_id=cluster-1" in target.read_text()


@requires_sdk
def test_down_refuses_a_cluster_it_did_not_create(env, monkeypatch):
    """A mistyped or stale id must not take out somebody's warehouse."""
    foreign = SimpleNamespace(id="prod-1", name="production-warehouse", tags=[])
    client = FakeClient(details=[foreign])
    monkeypatch.setattr(cluster, "build_client", lambda: client)

    assert cluster.main(["down", "--cluster-id", "prod-1"]) == 1
    assert client.deleted == []


@requires_sdk
def test_down_deletes_its_own_cluster(env, monkeypatch):
    owned = SimpleNamespace(
        id="cluster-1",
        name="datus-ci-42",
        tags=[tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE)],
    )
    client = FakeClient(details=[owned])
    monkeypatch.setattr(cluster, "build_client", lambda: client)

    assert cluster.main(["down", "--cluster-id", "cluster-1"]) == 0
    assert client.deleted == ["cluster-1"]


@requires_sdk
def test_down_force_skips_the_ownership_check(env, monkeypatch):
    # A cluster carrying neither the owner tag nor the name prefix: refused
    # without --force, deleted with it.
    foreign = detail("AVAILABLE", id="anything", name="production-warehouse", tags=[], public_ip=None)
    client = FakeClient(details=[foreign])
    monkeypatch.setattr(cluster, "build_client", lambda: client)

    assert cluster.main(["down", "--cluster-id", "anything", "--force"]) == 0
    assert client.deleted == ["anything"]


@requires_sdk
def test_down_propagates_a_non_404_inspection_failure(env, monkeypatch):
    """A permission or network error is not evidence the cluster is gone.

    Treating it as such would skip the delete and exit 0, leaving a billing
    cluster behind with nothing to signal it.
    """
    from huaweicloudsdkcore.exceptions.exceptions import SdkError, ServiceResponseException

    class Forbidden(FakeClient):
        def list_cluster_details(self, request):
            raise ServiceResponseException(403, SdkError(error_msg="permission denied"))

    client = Forbidden()
    monkeypatch.setattr(cluster, "build_client", lambda: client)

    with pytest.raises(ServiceResponseException):
        cluster.main(["down", "--cluster-id", "cluster-1"])
    assert client.deleted == []


# ==================== EIP release ====================
#
# RELEASE_BINDING only detaches; the address keeps billing. Two accumulated
# unnoticed before this was covered, so every delete decision is exercised.


class FakeEipClient:
    """Serves an EIP inventory and records deletions.

    Deleted addresses leave the inventory, as on the real API; a fake that kept
    serving them would let a double delete pass.
    """

    def __init__(self, publicips=()):
        self._publicips = list(publicips)
        self.deleted = []

    def list_publicips(self, request):
        return SimpleNamespace(publicips=list(self._publicips))

    def delete_publicip(self, request):
        self._publicips = [p for p in self._publicips if p.id != request.publicip_id]
        self.deleted.append(request.publicip_id)
        return SimpleNamespace()


def publicip(address, *, ip_id="eip-1", port_id=None):
    return SimpleNamespace(id=ip_id, public_ip_address=address, port_id=port_id)


@requires_sdk
@requires_eip_sdk
def test_release_eip_deletes_the_detached_address(monkeypatch):
    client = FakeEipClient([publicip("1.2.3.4", ip_id="eip-a")])

    assert cluster.release_eip("1.2.3.4", client=client) is True
    assert client.deleted == ["eip-a"]


@requires_sdk
@requires_eip_sdk
def test_release_eip_leaves_addresses_belonging_to_others(monkeypatch):
    """Exact address, never "any unattached EIP" — the account may hold its own."""
    client = FakeEipClient([publicip("9.9.9.9", ip_id="someone-else")])

    assert cluster.release_eip("1.2.3.4", client=client) is False
    assert client.deleted == []


@requires_sdk
@requires_eip_sdk
def test_release_eip_refuses_an_address_still_attached(monkeypatch):
    """A bound address means a live resource; cutting it is not ours to do."""
    client = FakeEipClient([publicip("1.2.3.4", ip_id="eip-a", port_id="port-7")])

    assert cluster.release_eip("1.2.3.4", client=client) is False
    assert client.deleted == []


@requires_sdk
@requires_eip_sdk
def test_down_releases_the_clusters_eip(env, monkeypatch):
    eip_client = FakeEipClient([publicip("1.2.3.4", ip_id="eip-a")])
    owned = detail(
        "AVAILABLE",
        id="cluster-1",
        name="datus-ci-run",
        tags=[tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE)],
        public_ip=SimpleNamespace(eip_address="1.2.3.4"),
    )
    client = FakeClient(details=[owned])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "build_eip_client", lambda: eip_client)
    monkeypatch.setattr(cluster, "wait_deleted", lambda *a, **k: None)

    assert cluster.main(["down", "--cluster-id", "cluster-1"]) == 0
    assert client.deleted == ["cluster-1"]
    assert eip_client.deleted == ["eip-a"]


@requires_sdk
@requires_eip_sdk
def test_down_without_an_eip_deletes_nothing_extra(env, monkeypatch):
    eip_client = FakeEipClient([publicip("1.2.3.4", ip_id="eip-a")])
    owned = detail(
        "AVAILABLE",
        id="cluster-1",
        name="datus-ci-run",
        tags=[tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE)],
        public_ip=None,
    )
    client = FakeClient(details=[owned])
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "build_eip_client", lambda: eip_client)

    assert cluster.main(["down", "--cluster-id", "cluster-1"]) == 0
    assert client.deleted == ["cluster-1"]
    assert eip_client.deleted == []


@requires_sdk
@requires_eip_sdk
def test_reap_releases_the_eip_of_an_abandoned_cluster(env, monkeypatch):
    """An abandoned cluster's EIP is what this backstop exists for."""
    expired = SimpleNamespace(
        id="expired-1",
        name="datus-ci-old",
        tags=[
            tag(cluster.OWNER_TAG_KEY, cluster.OWNER_TAG_VALUE),
            tag(cluster.EXPIRES_TAG_KEY, (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
        ],
    )
    eip_client = FakeEipClient([publicip("5.6.7.8", ip_id="eip-old")])
    client = FakeClient(
        clusters=[expired],
        details=[detail("AVAILABLE", public_ip=SimpleNamespace(eip_address="5.6.7.8"))],
    )
    monkeypatch.setattr(cluster, "build_client", lambda: client)
    monkeypatch.setattr(cluster, "build_eip_client", lambda: eip_client)
    monkeypatch.setattr(cluster, "wait_deleted", lambda *a, **k: None)

    assert cluster.main(["reap"]) == 0
    assert client.deleted == ["expired-1"]
    assert eip_client.deleted == ["eip-old"]


@requires_sdk
@requires_eip_sdk
def test_down_force_deletes_when_the_cluster_cannot_be_read(env, monkeypatch):
    """--force deletes despite not being able to inspect; the EIP is then
    undiscoverable, which the warning says rather than claiming a clean run."""
    from huaweicloudsdkcore.exceptions.exceptions import SdkError, ServiceResponseException

    class Forbidden(FakeClient):
        def list_cluster_details(self, request):
            raise ServiceResponseException(403, SdkError(error_msg="permission denied"))

    client = Forbidden()
    monkeypatch.setattr(cluster, "build_client", lambda: client)

    assert cluster.main(["down", "--cluster-id", "cluster-1", "--force"]) == 0
    assert client.deleted == ["cluster-1"]


@requires_sdk
@requires_eip_sdk
def test_release_unbound_eips_sweeps_leftovers_but_spares_attached_ones():
    """Collects addresses no cluster points at; they carry no owner tag.

    Safe because the account holds only these tests' resources — but an
    attached address is still off limits.
    """
    client = FakeEipClient(
        [
            publicip("1.1.1.1", ip_id="leftover-a"),
            publicip("2.2.2.2", ip_id="in-use", port_id="port-1"),
            publicip("3.3.3.3", ip_id="leftover-b"),
        ]
    )

    assert cluster.release_unbound_eips(client=client) == 2
    assert sorted(client.deleted) == ["leftover-a", "leftover-b"]


@requires_sdk
@requires_eip_sdk
def test_release_unbound_eips_dry_run_reports_without_deleting():
    client = FakeEipClient([publicip("1.1.1.1", ip_id="leftover-a")])

    assert cluster.release_unbound_eips(client=client, dry_run=True) == 1
    assert client.deleted == []
