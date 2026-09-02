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
| `HUAWEICLOUD_REGION` | `cn-east-3` | Region ID |
| `DWS_CI_VPC_ID` | `vpc-...` | Pre-created VPC |
| `DWS_CI_SUBNET_ID` | `subnet-...` | See the note below — a VPC subnet exposes *two* IDs |
| `DWS_CI_SECURITY_GROUP_ID` | `sg-...` | Must allow the DB port from wherever the job runs |
| `DWS_CI_AVAILABILITY_ZONE` | `cn-east-3a` | AZ inside the region |
| `DWS_CI_DB_PASSWORD` | — | Cluster admin password; must satisfy the DWS complexity rules |

Optional, with defaults: `DWS_CI_FLAVOR` (`dwsk2.h.xlarge.4.kc1`), `DWS_CI_NUM_NODE`
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

(If this job ever moves to a self-hosted runner inside the VPC, set
`DWS_CI_PUBLIC_IP=not_use`: no EIP, private endpoint, security group closed to
the internet. That is cheaper and safer, but it is not how the job runs today.)

## Security group rule

The cluster is reachable over the internet, so the security group must admit the
runner. Prefer, in this order:

1. **A self-hosted runner inside the VPC** with `DWS_CI_PUBLIC_IP=not_use`. No
   EIP, no inbound rule, nothing exposed. This is the only option that avoids
   the question rather than managing it.
2. **A hosted runner with a pinned source range**, if your runners have stable
   egress (a NAT gateway, a proxy, an enterprise fixed IP). Scope the rule to
   that range.
3. **A hosted runner with `0.0.0.0/0`** — what GitHub-hosted runners force,
   since their egress addresses are numerous and change. Treat this as a
   deliberate exception, not a default to reach for.

If you land on (3), what keeps it acceptable, and what to keep true:

- The cluster exists only for the minutes a run takes, and the reaper deletes
  anything that outlives its TTL — the port is not open between runs.
- It holds nothing but freshly loaded test fixtures.
- `DWS_CI_DB_PASSWORD` is the only thing standing in front of it, so treat it
  like any other production credential: long, random, and not reused.
- Set `DWS_SSLMODE=require` (or `verify-ca` with `DWS_SSLROOTCERT_PEM`) so the
  session is encrypted in transit — the adapter's own
  `test_connection_security.py` covers those modes.

A tighter variant, if this is worth revisiting later: give the CI user VPC write
permission and have the job add a rule for its own egress address before the
tests and remove it afterwards. That narrows the window to one IP, at the cost
of a wider IAM grant and more moving parts.

> **Which availability zone?** Ask the API instead of guessing — the reference's
> sample response shows a placeholder `az1` while its create-cluster sample uses
> `cn-north-7c`:
>
> ```bash
> uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 \
>   python ci/cloud/dws/cluster.py zones
> ```
>
> Use one of the printed `code` values. This call is read-only, so it is also the
> cheapest way to confirm a fresh AK/SK, project ID and region work at all —
> run it first, before anything that provisions.
>
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

- **Ownership guard.** `reap` and `down` both refuse a cluster that lacks the
  `datus-ci-owner` tag *or* the `datus-ci-` name prefix, so a mistyped or stale
  id cannot take out someone's warehouse. `down --force` overrides it for the
  rare case where the tags are gone.
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
export HUAWEICLOUD_SDK_AK=... HUAWEICLOUD_SDK_SK=... HUAWEICLOUD_PROJECT_ID=... HUAWEICLOUD_REGION=cn-east-3

# Read-only: proves the credentials work and prints the zone codes to choose from.
uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 python ci/cloud/dws/cluster.py zones
uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 python ci/cloud/dws/cluster.py flavors

export DWS_CI_VPC_ID=... DWS_CI_SUBNET_ID=... DWS_CI_SECURITY_GROUP_ID=... DWS_CI_AVAILABILITY_ZONE=cn-east-3a
export DWS_CI_DB_PASSWORD=...

uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 python ci/cloud/dws/cluster.py up
# ... run tests against the printed host/port ...
uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 python ci/cloud/dws/cluster.py down --cluster-id <id> --wait

# See what the reaper would remove, without removing it:
uv run --no-project --isolated --with huaweicloudsdkdws==3.1.212 python ci/cloud/dws/cluster.py reap --dry-run
```

`ci/tests/test_dws_cluster.py` covers the decision logic (naming, TTL, the
ownership guard, polling, teardown-on-failure) against fake clients, so it runs
without cloud credentials.

## Verified end to end

Run against a real cn-east-3 account: `up` created a 3-node
`dwsk2.h.xlarge.4.kc1` cluster (about 17 minutes), tagged it with its owner and
expiry, auto-assigned an EIP, and emitted the connection details; the adapter's
integration suite then passed against it — **29 passed, 3 skipped in 9.9s** (the
skips need the optional `DWS_SSLROOTCERT_PEM`).

One thing that will bite anyone repeating this: the security group must actually
admit the runner. Without the inbound rule every test waits out a TCP timeout,
turning a 10-second suite into a 6-minute one that fails with
`Operation timed out` rather than anything about permissions.

## Region-specific values, and a misleading error

Three fields must match what the target region actually offers. Their defaults
suit cn-east-3 and are almost certainly wrong elsewhere:

| Variable | Default | Where the real value comes from |
|---|---|---|
| `DWS_CI_FLAVOR` | `dwsk2.h.xlarge.4.kc1` | Console → create cluster → node flavor |
| `DWS_CI_DATASTORE_VERSION` | `9.1.0.227` | Console → create cluster → cluster version |

The cloud workflow runs a matrix over datastore versions (`9.1.0.227` and
`8.2.1.258` today), provisioning one cluster per leg — the majors differ enough
that passing on one says little about the other. `DWS_CI_NAME_SUFFIX` keeps the
cluster names distinct; without it the second leg would collide with the first.
Legs are `fail-fast: false`, so a version-specific failure does not hide the
others. In long-lived-cluster mode only the first leg runs, since that cluster
has whatever version it has.

cn-east-3 currently offers `9.1.0.227`, `9.1.1.305` and `8.2.1.258`; all three
were verified to be accepted with `dwsk2.h.xlarge.4.kc1`.
| `DWS_CI_AVAILABILITY_ZONE` | — | `cluster.py zones`, or the subnet's AZ |

Budget time for this: **an unavailable flavor and a missing `datastore_version`
are both reported as `DWS.5207 Number of CN instances is invalid!`** — an error
that names a field having nothing to do with either. If you see it, the CN count
is the least likely cause; check the flavor and version against the console
first. A version that exists but is wrong at least says `DWS.5003`.

`list_node_types` is not a substitute for the console here: it returns
`dwsk2.xlarge` where the console (and the API) want `dwsk2.h.xlarge.4.kc1`, and
it carries no version information at all.
