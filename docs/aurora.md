# Aurora authentication and connection lifecycle

Aurora MySQL uses `datus-mysql`; Aurora PostgreSQL uses `datus-postgresql`.
The SQL dialect, metadata and result contracts remain MySQL and PostgreSQL.
This implementation uses the normal database protocol, not Data API, Aurora
DSQL, Babelfish, or automatic transaction recovery.

## Installation and configuration

Password authentication needs no AWS dependency. For IAM or Secrets Manager:

```sh
pip install 'datus-mysql[aws]'       # or 'datus-postgresql[aws]'
```

These changes require the matching workspace packages: `datus-db-core` 0.1.8,
`datus-sqlalchemy` 0.1.10, `datus-mysql` 0.1.9 / `datus-postgresql` 0.1.10.
Publish core, SQLAlchemy, then the adapters before installing from PyPI.

Example Datus Agent configuration (adapter-specific fields travel through
`DbConfig.extra`; no Aurora-specific Agent dispatch is required):

```yaml
agent:
  services:
    datasources:
      aurora_mysql:
        type: mysql
        host: ${AURORA_MYSQL_ENDPOINT}
        port: 3306
        username: ${AURORA_DB_USER}
        database: ${AURORA_DATABASE}
        aurora: true
        auth_mode: iam
        aws:
          region: ${AWS_REGION}
          profile: ${AWS_PROFILE}
        ssl_ca: ${AURORA_CA_BUNDLE}
        connect_timeout_seconds: 10
      aurora_postgresql:
        type: postgresql
        host: ${AURORA_POSTGRESQL_ENDPOINT}
        port: 5432
        username: ${AURORA_DB_USER}
        database: ${AURORA_DATABASE}
        schema: public
        aurora: true
        auth_mode: iam
        aws:
          region: ${AWS_REGION}
          profile: ${AWS_PROFILE}
        sslrootcert: ${AURORA_CA_BUNDLE}
        sslmode: verify-full
        connect_timeout_seconds: 10
```

Omit `aws.profile` in EC2/ECS/EKS or other deployments using the SDK's default
credential chain. For password authentication set `auth_mode: password` and
`password: ${AURORA_PASSWORD}`. Keep `aurora: true` and the database CA settings.
The programmatic connector accepts the datasource mapping without `type`.

| Field | Default | Contract |
| --- | --- | --- |
| `aurora` | `false` | Explicitly enables strict Aurora TLS, including password authentication |
| `auth_mode` | `password` | `password` or `iam`; IAM cannot include `password` or `secret_ref` |
| `secret_ref` | unset | Secrets Manager name/ARN, used in password mode; cannot include an inline password |
| `secret_cache_ttl_seconds` | `300` | Positive cache lifetime, at most 86400 seconds |
| `aws.region` | SDK default | AWS service/signing region |
| `aws.profile` | SDK default | SDK profile, including an AssumeRole profile |
| `aws.ca_bundle` | SDK default | CA bundle for AWS HTTPS APIs; distinct from database CA |
| `timeout_seconds` | `30` | Pool wait timeout and fallback driver connection timeout |
| `connect_timeout_seconds` | unset | Positive driver connection timeout override |
| MySQL `read_timeout_seconds` / `write_timeout_seconds` | unset | Driver socket timeouts; not an overall query deadline |

Existing ordinary MySQL/PostgreSQL configurations keep their TLS defaults.
IAM requires verified TLS even if `aurora` is omitted. MySQL requires `ssl_ca`
and enables `ssl_verify_cert` plus `ssl_verify_identity`. PostgreSQL requires
`sslrootcert` and uses `sslmode=verify-full`. Explicit weaker settings are rejected.
MySQL also accepts `ssl_cert`/`ssl_key`; PostgreSQL accepts `sslcert`/`sslkey`.
Client certificate and key must be supplied together.

Connection timeout, pool wait, socket read/write timeout and query cancellation
are different controls. These adapters do not add a new overall query deadline.

For Serverless v2 with automatic pause, allow time for a paused instance to
resume: use `connect_timeout_seconds: 60` for these disposable tests. MySQL also
uses `read_timeout_seconds: 60` and `write_timeout_seconds: 60` in the cloud test
configuration, so a stale network connection or a stalled greeting cannot hang
the job indefinitely. Socket read timeouts also apply while reading a query
result; choose them according to the application's query durations. AWS describes
typical resume delays of about 15 seconds, or 30 seconds or more after extended
inactivity in its [auto-pause documentation](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2-auto-pause.html).

## IAM and AWS identity prerequisites

1. Enable IAM database authentication on the target cluster.
2. Create a database user enabled for IAM (`AWSAuthenticationPlugin` on Aurora
   MySQL; `rds_iam` on Aurora PostgreSQL), and grant the required SQL privileges.
3. Grant the runtime AWS identity `rds-db:connect` for the cluster resource ID
   and that exact database username. Cross-account access must assume a role
   in the database account with this permission.
4. Download the appropriate AWS RDS CA bundle and mount it at the configured path.
5. Use the AWS cluster writer or reader endpoint as `host`. IAM signing uses
   exactly this host/port/user. A custom Route 53 alias cannot replace the AWS
   endpoint for token generation.

Example read-only database grants, run by the database administrator (replace
`app` and the schema with the intended scope):

```sql
-- Aurora MySQL
CREATE USER 'datus_iam' IDENTIFIED WITH AWSAuthenticationPlugin AS 'RDS';
ALTER USER 'datus_iam'@'%' REQUIRE SSL;
GRANT SELECT ON app.* TO 'datus_iam'@'%';

-- Aurora PostgreSQL, connected to app
CREATE USER datus_iam;
GRANT rds_iam TO datus_iam;
GRANT CONNECT ON DATABASE app TO datus_iam;
GRANT USAGE ON SCHEMA public TO datus_iam;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datus_iam;
```

Use a separate password user when verifying both authentication modes. Grant
additional DDL/DML privileges only for a disposable write-contract test database.
Attach an IAM policy such as the following to the runtime identity, replacing
the region, account, **DbClusterResourceId** and case-sensitive database username:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "rds-db:connect",
    "Resource": "arn:aws:rds-db:us-east-1:123456789012:dbuser:cluster-REPLACE_WITH_RESOURCE_ID/datus_iam"
  }]
}
```

The resource ID is different from the cluster name; retrieve it from the cluster's
Configuration tab or `aws rds describe-db-clusters`. See AWS's
[database account setup](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.DBAccounts.html)
and [IAM policy format](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.IAMPolicy.html).

For cross-account access, use SDK shared configuration, for example:

```ini
[profile aurora-cross-account]
role_arn = arn:aws:iam::123456789012:role/datus-aurora
source_profile = developer
region = us-east-1
```

Use `aws.profile: aurora-cross-account`. The SDK owns AssumeRole and refreshes
its temporary credentials. The adapter does not copy a one-time STS response
into another static Session. Runtime roles and web identity also use the SDK
credential provider chain. MFA profiles require interactive acquisition before
use in an unattended service.

Tokens are generated on every new physical connection via SQLAlchemy
`do_connect`, including the parent and every per-database PostgreSQL engine.
They are not stored in engine URLs. MySQL passes the `mysql_clear_password`
authentication plugin mapping. Working pooled sessions are reused after a
token expires; new physical connections obtain a new token.

Sources: [AWS Python examples](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.Connecting.Python.html),
[SDK credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html),
[SQLAlchemy dynamic authentication](https://docs.sqlalchemy.org/en/20/core/engines.html#generating-dynamic-authentication-tokens).

## Secret rotation, pool ownership and failure handling

For Secrets Manager password authentication replace `auth_mode: iam` with:

```yaml
auth_mode: password
secret_ref: ${AURORA_SECRET_ARN}
secret_cache_ttl_seconds: 300
```

The identity needs `secretsmanager:GetSecretValue` and, where applicable,
`kms:Decrypt`. The Secret must be a JSON object containing a string `password`.
If it contains `username`, that value must equal the configured database user.
The adapter uses `AWSCURRENT` and tracks `VersionId`. Binary Secrets and rotation
that switches database usernames are not accepted by this configuration.
Host/port/database are taken from the datasource, not replaced by Secret fields.

Before each operation's engine lookup, an expired Secret cache is refreshed.
A new version disposes all idle pools belonging to that connector, including
PostgreSQL's base pool and all cached database pools. New connections use the
new password. `connector.refresh_credentials()` forces a refresh without waiting
for the TTL. Refresh failure after expiry fails that operation; it does not use
an expired cached password to establish another connection. An unchanged version
does not churn pools.

An operation already holding a connection is allowed to finish. Disposing a
pool does not cancel an active transaction. Each connector owns its own Session,
Secret cache and pools; no pool is shared between datasources or users.
Authentication is snapshotted for a connector's lifetime. When identity, database
user, endpoint or TLS configuration changes, create a replacement connector and
close the old one; mutating `connector.config` is not a reconfiguration API.
SDK credential renewal for the same identity does not require pool disposal.

The shared `AWSIdentity` accepts an injected SDK Session and fixed botocore client
options, so Athena can apply its socket timeouts and retry policy through the
same provider. Closing an Aurora or Athena connector releases its cached AWS
clients. The Session and its credential refresh state survive later reconnection;
cleanup attempts every client and does not expose SDK error messages.

Use distinct datasource entries for writer and reader endpoints; there is no
automatic SQL routing. With `aurora: true`, canonical AWS cluster writer
endpoints also validate the server role on every pool checkout. MySQL uses
`@@innodb_read_only`; PostgreSQL uses `pg_is_in_recovery()`. A demoted server
causes SQLAlchemy's bounded pool reconnect path to run before application SQL.
The probe's transaction is rolled back before the application transaction starts.
Reader, custom and instance endpoints have no implicit writer-role requirement.
IAM alone does not enable Aurora-specific role checks for other RDS engines.
See the [Aurora MySQL role guidance](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraMySQL.BestPractices.html).

`pool_pre_ping` invalidates stale connections, and each new physical connection
resolves the endpoint again. The OS resolver must honor Aurora's DNS TTL;
checkout can still fail while DNS points to a demoted server. Initial connection errors
are returned to the caller. There is no application-level SQL/transaction replay,
including writes whose commit outcome is unknown. AWS errors expose diagnostic
codes without SDK response messages; dynamic driver login errors redact passwords
and tokens while retaining driver error types/codes.

## Offline and container validation

Run unit suites separately, as required by the adapter testing standard:

```sh
ci/run-unit-tests.sh datus-db-core datus-sqlalchemy datus-mysql datus-postgresql
```

The SDK is a development-only dependency for Stubber tests. The suites cover
SDK credential renewal, Secret TTL/version/error handling, token injection into
every engine, pooled-session reuse, identity isolation, TLS parameters, redaction,
and absence of write replay. Other adapter installations do not require boto3.

From the workspace root, run the same isolated TLS jobs used in CI:

```sh
uv run --no-project python -m ci.run_tls_integration_tests mysql
uv run --no-project python -m ci.run_tls_integration_tests postgresql
```

These jobs generate two-day disposable certificates, start a unique Compose
project on a random loopback port, perform authenticated verified-TLS readiness,
run the ordinary contract/TPC-H/integration suite plus wrong-CA/hostname cases,
and remove only their own containers, volumes and certificates. PostgreSQL also
exercises multiple databases. They require Docker Compose v2, OpenSSL and uv.
Ordinary Docker suites continue using `ci/run-integration-tests.sh mysql postgresql`.

## Real Aurora acceptance

Set `DATUS_AURORA_MYSQL_CONFIG` or `DATUS_AURORA_POSTGRESQL_CONFIG` to a complete
JSON adapter configuration (without `type`), including `aurora: true`, the AWS
endpoint, database user, database and CA path. Supply credentials via an appropriate
runtime identity/SDK profile; an inline database password, if used, must come from
the test environment rather than a checked-in file.

```sh
uv run --all-packages pytest datus-mysql/tests/integration/test_aurora_cloud.py -m integration
uv run --all-packages pytest datus-postgresql/tests/integration/test_aurora_cloud.py -m integration
```

Run each engine with password and IAM configurations. Missing cloud configuration
skips before connector creation; a configured connection/API/assertion failure fails.
The default case executes Chinese/NULL results and database metadata discovery.
With IAM it also confirms that an intentionally invalid token is rejected, after
first establishing that the valid configuration works.

Additional explicitly enabled cases:

| Case | Environment and prerequisites |
| --- | --- |
| Token lifetime | `DATUS_AURORA_LONG_TESTS=1`, `auth_mode=iam`; query the same session every minute for 16 minutes, reject a new physical connection using the expired token, then generate a fresh token and reconnect |
| SDK role expiration | Also set `DATUS_AURORA_ROLE_EXPIRY_TEST=1` with an SDK AssumeRole profile whose `duration_seconds=900`; require the original STS session to really expire and the provider to refresh it before reconnection |
| Secret rotation | `DATUS_AURORA_MUTATION_TESTS=1`, `secret_ref`; use a disposable Secret with a functioning single-user rotation Lambda, or set `DATUS_AURORA_ROTATION_CLUSTER_ID` for Aurora's managed master Secret |
| Writer failover | `DATUS_AURORA_MUTATION_TESTS=1`, `DATUS_AURORA_MYSQL_CLUSTER_ID` or `DATUS_AURORA_POSTGRESQL_CLUSTER_ID`; disposable cluster with a promotable replica, writer endpoint, DescribeDBClusters/FailoverDBCluster permissions |
| Reader endpoint | The same disposable-cluster settings; require an actual reader, query it through its endpoint, and verify it rejects DDL |
| IAM permission rejection | IAM config plus `DATUS_AURORA_MYSQL_DENIED_PROFILE` or `DATUS_AURORA_POSTGRESQL_DENIED_PROFILE`; a test-only role that explicitly denies `rds-db:connect` |

The cloud suites also run the shared deep adapter contract and reject an untrusted
CA and an IP address that does not match the Aurora certificate. The trust test
generates a one-day unrelated certificate with OpenSSL. No DNS aliases are created.

Managed master rotation verifies that the target cluster owns the configured
Secret before calling `ModifyDBCluster(RotateMasterUserPassword=True,
ApplyImmediately=True)`. Grant DescribeSecret/GetSecretValue and
DescribeDBClusters/ModifyDBCluster for that test environment. This path needs no
rotation Lambda. The standalone Secret path uses DescribeSecret/RotateSecret.

Mutation cases change the assigned test environment. They do not create or delete
clusters. Rotation waits for the new version to become `AWSCURRENT`; failover waits
for a different writer and verifies reconnection with a read-only probe.
The failover case keeps the pre-failover pools and does not manually close the
connector: recovery must happen through the adapter's normal connection path.
Allow 15 minutes for each lifecycle transition, plus the 16-minute token case.

Local certificates, Stubber and container results do not prove real Aurora IAM,
rotation, failover or service-version compatibility. `aurora-mysql` and
`aurora-postgresql` are registered but disabled cloud CI targets until clusters,
scoped credentials and their workflows are assigned (VERIFY-01.4).

## Validation (2026-10-16)

Local validation also passed: MySQL 8.0.44 verified-TLS integration (35 tests),
PostgreSQL 16.11 verified-TLS integration (41 tests), both isolated TLS CI runners,
CI infrastructure tests (159 passed, 28 existing optional-environment skips), and
wheel/sdist builds plus isolated imports for the four changed packages. Agent
DBManager regression passed 86 tests against upstream `bf3e6a6`.

The Agent integration additionally exercised SQLAlchemy 2.0.23 against a read-only
upstream source snapshot. This verifies configuration and DBManager integration;
a full LLM Agent journey and live cross-account trust policies were not tested.
Only the Aurora engine versions listed below were exercised.

### Real AWS validation

Disposable resources are owned by a uniquely tagged CloudFormation stack, using
the existing default VPC and two public subnets. The database security group
accepts only the local runner's IPv4 `/32`. Each engine starts with one
`db.serverless` writer, Aurora Standard storage, capacity 0–1 ACUs and a
300-second auto-pause interval. A reader is added only after the token-expiration
case, immediately before reader/failover acceptance. The cluster has explicit
`DeletionPolicy: Delete`; cleanup removes the stack, managed master Secret and
the two temporary IAM roles. No NAT gateway, RDS Proxy, Lambda or custom KMS key
is provisioned.

| Real service check | Aurora MySQL 8.0.mysql_aurora.3.13.0 | Aurora PostgreSQL 16.15 |
| --- | --- | --- |
| Ordinary integration, shared contract and TPC-H | 32 passed, 0 skipped | 38 passed, 0 skipped |
| Secret authentication, contract, TLS rejection, native master rotation | 5 passed, 0 skipped | 5 passed, 0 skipped |
| IAM authentication, contract, TLS/token/permission rejection | 6 passed; 6 passed again on the rebuilt instance | 6 passed, 0 skipped |
| Actual token and 900-second SDK role session expiration | 1 passed, 0 skipped | 1 passed, 0 skipped |
| Reader endpoint and writer failover, retaining pre-failover pools | 2 passed after correcting the Aurora MySQL role probe | 2 passed after adding the checkout role validator |
| Agent configuration → registry → DBManager → verified-TLS query/metadata | 2 passed after the fix; 1 additional test matches the promoted instance ID | 2 passed after the fix; SQLAlchemy 2.0.23, including multiple databases |
| Post-fix Secret query and shared contract | 2 passed after correcting the local DNS cache | 2 passed |
| Final unit regression | 21 packages, 1921 passed; SQLAlchemy 57, MySQL 81, PostgreSQL 107, core 293 | Same run |
| Resource deletion and post-delete inventory | Both environments deleted; 18 checks passed | Deleted; 9 checks passed |

The final accepted suites contain 107 case executions (MySQL 51, PostgreSQL 56), all passed with zero skips, including post-fix regressions. Three environments, including the MySQL rebuild, passed all 27 post-delete inventory checks at 10:52 UTC. No test instances, clusters, snapshots, retained automated backups, managed Secrets, dedicated subnet/security groups or temporary IAM roles remain.

The initial MySQL lifetime case left its TCP connection completely idle. Database
connections fell to zero after roughly six minutes and Serverless subsequently
auto-paused; the client's stale read hung until the 20-minute job timeout. The
specific intermediate network device was not identified. The revised case
queries and checks the same physical session every minute while still waiting
for actual token and STS expiration. It fails if a heartbeat moves to another
session. This separates authentication lifetime from idle-network retention;
production connection/authentication behavior was not changed for this incident.

Reader/failover acceptance exposed two further issues. Control-plane availability
does not imply the reader endpoint's DNS is ready, so reader setup waits with new
read-only connections. A still-readable connection to a former writer must not
remain in the writer pool: checkout now validates the server role, retaining the
pre-failover pools in the test. Aurora MySQL role detection uses
`innodb_read_only`, because `read_only` was zero on both real nodes.

After the MySQL failover, the macOS system resolver intermittently returned the
former writer's address even though default `dig`, Cloudflare and Google DNS
returned the promoted instance with a five-second CNAME TTL. The role validator
rejected those connections before business SQL. `dscacheutil -flushcache` restored
consistent system resolution; the Secret shared contract, Agent password/IAM
paths and a live `@@aurora_server_id` match then passed. Earlier timeout,
interrupted and failed attempts are retained in the local reports. Runtime code
does not flush OS DNS caches, query an alternative resolver, or replay SQL.

JUnit reports and a credential-free resource manifest are retained locally under
the run artifact directory. Live test configuration references Secrets Manager;
database passwords and STS credentials are not written into those artifacts.
Cloud CI still requires its own provisioning workflow and scoped credentials.
