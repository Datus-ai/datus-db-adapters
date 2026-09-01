# Ephemeral DWS clusters for CI

GaussDB(DWS) bills per hour and has no self-service serverless tier, so the
integration job creates a cluster for the run and deletes it afterwards rather
than keeping one online. `cluster.py` implements that lifecycle.

Deleting is the only operation Huawei Cloud documents as stopping billing
outright: a *stopped* cluster still bills its disks, and for single-tier
cloud-disk flavors (which includes the smallest one) the docs do not state
whether nodes stop billing at all.

## The two modes

`.github/workflows/dws-cloud-tests.yml` picks a mode from the secrets present:

| Mode | Trigger | Behavior |
|---|---|---|
| long-lived | `DWS_HOST` is set | Connects to a cluster somebody keeps online. No provisioning. |
| ephemeral | `DWS_HOST` unset, `HUAWEICLOUD_SDK_AK` set | Creates a cluster, runs the tests, deletes it in an `always()` step. |

Ephemeral runs cost roughly one cluster-hour per run (creation alone takes
10–15 minutes) instead of 720 per month.

## Required secrets

**Ephemeral mode** — all required:

| Secret | Example | Notes |
|---|---|---|
| `HUAWEICLOUD_SDK_AK` | `ABCD...` | IAM access key ID |
| `HUAWEICLOUD_SDK_SK` | — | IAM secret access key |
| `HUAWEICLOUD_PROJECT_ID` | `05f2...` | Project ID **of the target region** (My Credentials → API Credentials) |
| `HUAWEICLOUD_REGION` | `cn-north-4` | Region ID |
| `DWS_CI_VPC_ID` | `vpc-...` | Pre-created VPC |
| `DWS_CI_SUBNET_ID` | `subnet-...` | See the note below — a VPC subnet exposes *two* IDs |
| `DWS_CI_SECURITY_GROUP_ID` | `sg-...` | Must allow the DB port from wherever the job runs |
| `DWS_CI_AVAILABILITY_ZONE` | `cn-north-4a` | AZ inside the region |
| `DWS_CI_DB_PASSWORD` | — | Cluster admin password; must satisfy the DWS complexity rules |

Optional, with defaults: `DWS_CI_FLAVOR` (`dwsx2.xlarge.m7`), `DWS_CI_NUM_NODE`
(`3` — the documented minimum for cluster mode), `DWS_CI_DB_NAME` (`gaussdb`),
`DWS_CI_DB_USER` (`dbadmin`), `DWS_CI_DB_PORT` (`8000`), `DWS_CI_TTL_MINUTES`
(`180`), `DWS_CI_PUBLIC_IP` (`auto_assign`), `DWS_CI_EIP_BANDWIDTH` (`5` Mbit/s).

## What costs money, and what to keep

**Keep these permanently — they are free to hold:** the VPC, its subnet and the
security group. Huawei Cloud does not bill for the network objects themselves
(only for traffic-carrying add-ons like NAT gateways, VPN or bandwidth), so
recreating them per run would add failure modes and IDs to rotate for no saving.
Create them once, put their IDs in secrets, leave them alone.

**These are billed and so are created and destroyed per run:** the cluster
itself, and its EIP when one is assigned.

The EIP needs care. `DWS_CI_PUBLIC_IP` defaults to `auto_assign` because the
cloud job runs on `ubuntu-latest`, a GitHub-hosted runner on the public
internet, which has no route to a VPC-private address. The delete call therefore
passes `release_eip_type=RELEASE_BINDING`: the API's own default is
`NO_RELEASE`, which would leave the EIP behind, unattached and still billing,
after its cluster was gone.

If you move this job to a self-hosted runner inside the VPC, set
`DWS_CI_PUBLIC_IP=not_use` — no EIP is created, the private endpoint is used
instead, and the security group can stay closed to the internet. That is both
cheaper and safer; the internet path exists only because the job runs on a
hosted runner today.

> **Security group scope.** With a hosted runner the cluster port must be
> reachable from GitHub's egress addresses, which are numerous and change. Weigh
> that before opening the port broadly: an in-VPC runner (above) avoids the
> question entirely, and the port is only ever open for the minutes a cluster
> exists.

> **Which availability zone?** Ask the API instead of guessing — the reference's
> sample response shows a placeholder `az1` while its create-cluster sample uses
> `cn-north-7c`:
>
> ```bash
> uv run --no-project --isolated --with huaweicloudsdkdws \
>   python ci/cloud/dws/cluster.py zones
> ```
>
> Use one of the printed `code` values. This call is read-only, so it is also the
> cheapest way to confirm a fresh AK/SK, project ID and region work at all —
> run it first, before anything that provisions.

> **Which subnet ID?** A VPC subnet's detail page shows both a *subnet ID* and a
> *network ID*, and different Huawei Cloud services want different ones. Use the
> **subnet ID**: the DWS API reference calls this parameter "集群子网ID"
> throughout and never mentions a network ID (zero occurrences in the 1634-page
> PDF). If a create call is rejected for an invalid subnet, try the network ID
> from the same page — a rejected create fails immediately and provisions
> nothing, so the experiment is free.

**Long-lived mode**: `DWS_HOST`, `DWS_DATABASE`, `DWS_USERNAME`, `DWS_PASSWORD`,
plus the optional `DWS_PORT` / `DWS_SCHEMA` / `DWS_SSLMODE` /
`DWS_SSLROOTCERT_PEM`.

## Creating the access key

1. Create a **dedicated IAM user** for CI — do not use the account's own key.
2. Grant it, scoped to the target region:
   - `DWS FullAccess` (or at minimum `dws:cluster:create`, `dws:cluster:delete`,
     `dws:cluster:getDetail`, `dws:cluster:list`)
   - `VPC ReadOnlyAccess` — creating a cluster references an existing VPC,
     subnet and security group
3. My Credentials → Access Keys → Create Access Key. The secret is shown once;
   store both halves as repository secrets immediately.
4. Pre-create the VPC, subnet and security group the cluster will join, and
   record their IDs. The security group must permit the DB port from the runner.

Note the privilege difference between the modes: long-lived mode only needs
database credentials, while ephemeral mode needs an IAM key that can create and
delete clusters.

## Safety properties

- **Ownership guard.** `reap` and every delete path only touch clusters that
  carry the `datus-ci-owner` tag *and* a `datus-ci-` name prefix. A cluster
  missing either is never considered.
- **TTL backstop.** Each cluster is tagged with an expiry instant.
  `.github/workflows/dws-reaper.yml` runs hourly and deletes CI clusters past
  theirs, covering the case where the job's `always()` teardown never ran (a
  cancelled runner, a lost network). An unparseable expiry tag counts as
  expired — nothing else would clean that cluster up.
- **Failed creates are cleaned up.** A cluster that never reaches `AVAILABLE`
  is deleted before the failure is reported; it bills either way.
- **Fail fast on bad states.** Any status outside the known pending set aborts
  immediately instead of waiting out the timeout.

## Local use

```bash
export HUAWEICLOUD_SDK_AK=... HUAWEICLOUD_SDK_SK=... HUAWEICLOUD_PROJECT_ID=... HUAWEICLOUD_REGION=cn-north-4

# Read-only: proves the credentials work and prints the zone codes to choose from.
uv run --no-project --isolated --with huaweicloudsdkdws python ci/cloud/dws/cluster.py zones

export DWS_CI_VPC_ID=... DWS_CI_SUBNET_ID=... DWS_CI_SECURITY_GROUP_ID=... DWS_CI_AVAILABILITY_ZONE=cn-north-4a
export DWS_CI_DB_PASSWORD=...

uv run --no-project --isolated --with huaweicloudsdkdws python ci/cloud/dws/cluster.py up
# ... run tests against the printed host/port ...
uv run --no-project --isolated --with huaweicloudsdkdws python ci/cloud/dws/cluster.py down --cluster-id <id> --wait

# See what the reaper would remove, without removing it:
uv run --no-project --isolated --with huaweicloudsdkdws python ci/cloud/dws/cluster.py reap --dry-run
```

`ci/tests/test_dws_cluster.py` covers the decision logic (naming, TTL, the
ownership guard, polling, teardown-on-failure) against fake clients, so it runs
without cloud credentials.

**Not yet exercised against the live API.** The SDK call shapes were checked
against `huaweicloudsdkdws`' models, but no cluster has been created through
this tool. Run `up` once manually — with the reaper's TTL as a safety net —
before enabling the target in `ci/integration-targets.toml`.
