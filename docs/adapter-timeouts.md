# Adapter timeout policy and audit

This documents the fix for [issue #165](https://github.com/Datus-ai/datus-db-adapters/issues/165)
and a source audit of the other adapters at `e959c5e` (2026-10-22).

## Recommended production configuration

Use the **interactive** profile for backend requests that execute short SQL and
return bounded results. Use the **analytical** profile for explicitly selected
reporting queries that may take up to two minutes. These are starting policies,
not vendor defaults or values established by the integration benchmarks below.
The best values depend on the request latency budget, observed healthy query
latency, transaction duration, result size and network conditions. Cloud-service
profiles below have configuration-level validation only, as described in the
validation section.

All values below are **seconds**. Merge each fragment into the adapter's existing
connection configuration; host, authentication and namespace fields are omitted.
Only pass fields supported by that adapter. The examples describe adapter input,
not an assumed backend YAML schema.

### MySQL, StarRocks, Doris and TiDB

Recommended interactive fragment:

```json
{
  "timeout_seconds": 30,
  "connect_timeout_seconds": 5,
  "read_timeout_seconds": 30,
  "write_timeout_seconds": 10,
  "query_timeout_seconds": 20
}
```

| Setting | Interactive | Analytical | Reason for the size |
| --- | ---: | ---: | --- |
| `timeout_seconds` | 30 | 30 | Keeps pool checkout and unspecified transport bounds finite without increasing pool contention waits just because a query is longer. Thirty seconds is an initial ceiling; shorten it if the backend's queueing budget is smaller. |
| `connect_timeout_seconds` | 5 | 10 | A healthy nearby endpoint should connect well inside five seconds. Ten seconds allows more connection-establishment latency for remote analytical services; increase only when healthy connection measurements justify it. This is not a bound on the entire authentication/DNS path. |
| `read_timeout_seconds` | 30 | 150 | Leaves 10 seconds after the 20-second query limit, or 30 seconds after the 120-second limit, to receive a server timeout/error and absorb network scheduling delays. It also bounds a stalled socket. |
| `write_timeout_seconds` | 10 | 30 | Short SQL requests should not need a long socket-write wait. Thirty seconds gives larger analytical SQL/parameter payloads more room; bulk ingestion needs its own measured policy. |
| `query_timeout_seconds` | 20 | 120 | Twenty seconds fits short interactive queries and limits expensive accidental scans. Two minutes permits intentionally longer reports without making every request wait that long. These limits apply to the engine's supported SELECT execution policy. |

Set `query_timeout_seconds` explicitly when the application owns the SELECT
deadline. Its `None` default **preserves the server policy** in this family; it
does not inherit `timeout_seconds`. If an administrator already maintains the
desired session policy, omit this override instead. Confirm the engine/version
supports the session variable before adopting it; unsupported settings fail
connection setup. The other three overrides inherit `timeout_seconds` when
`None`, even though the config object still displays `None`.

The 10/30-second read margin is a starting allowance, not a cancellation
guarantee. A socket read limit measures individual reads, so result streaming can
continue longer than that value. DDL/DML need their own engine-supported limits;
the SELECT limit does not bound every operation.

### PostgreSQL, Greenplum, GaussDB, DWS and Hologres

Recommended interactive fragment:

```json
{
  "timeout_seconds": 30,
  "connect_timeout_seconds": 5,
  "read_timeout_seconds": 30,
  "query_timeout_seconds": 20
}
```

For the analytical profile, keep `timeout_seconds=30`, and use
`connect_timeout_seconds=10`, `read_timeout_seconds=150` and
`query_timeout_seconds=120`. The connection and query sizes have the same
latency/resource tradeoffs as the MySQL-family table above. Here the read value
is a **total connection-checkout lease**, including statements, fetches and
reset. Thirty seconds suits one short statement plus cleanup; 150 seconds allows
one two-minute statement plus 30 seconds of other work on the borrowed
connection. It is not renewed by incoming packets or by starting another SQL
statement. Size the lease for the whole transaction if it contains multiple
statements or substantial application work; a late statement may have less time
remaining than its server limit. There is no separate `write_timeout_seconds`
field for this family.

Both read and query overrides inherit `timeout_seconds` when `None`. Setting
them explicitly avoids giving the server and socket timer identical budgets,
which can close the socket before the server's timeout error arrives. PostgreSQL
defines the server limit per statement in its
[statement timeout documentation](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-STATEMENT-TIMEOUT);
the connection lease is an additional adapter policy.

### Other repaired adapters

| Adapter(s) | Interactive fragment | Analytical fragment | Reason and configuration limit |
| --- | --- | --- | --- |
| Hive, Spark | `{"timeout_seconds":30,"query_timeout_seconds":20}` | `{"timeout_seconds":150,"query_timeout_seconds":120}` | Allows the same 10/30-second response margin around a blocking Thrift statement. `timeout_seconds` also controls pool checkout and transport connection setup; this interface cannot retain a five-second connect limit while increasing only socket reads. The server must implement `queryTimeout`. |
| ClickHouse, Trino | `{"timeout_seconds":30,"query_timeout_seconds":20}` | `{"timeout_seconds":150,"query_timeout_seconds":120}` | A blocking HTTP response needs room for the query and the error/response margin. The shared value also increases HTTP connection and pool waits; do not use the analytical profile on a short-latency request path unless those waits fit its budget. |
| Oracle | `{"timeout_seconds":5,"call_timeout_seconds":30}` | `{"timeout_seconds":10,"call_timeout_seconds":150}` | Keeps TCP connection and pool waits to 5/10 seconds while allowing a database round trip 30/150 seconds. There is no query-versus-read margin to configure: the exposed call limit covers a single round trip, not total statement duration. |
| BigQuery | `{"timeout_seconds":30,"query_timeout_seconds":20}` | `{"timeout_seconds":60,"query_timeout_seconds":120}` | Thirty seconds bounds an interactive REST call/retry budget; 60 seconds preserves the adapter's default API allowance for remote analytical jobs. The separate 20/120-second value sets foreground query waiting and the server job setting. Polling allows the analytical job to outlast an individual HTTP request. |
| ClickZetta | `{"timeout_seconds":30,"query_timeout_seconds":20}` plus the options below | `{"timeout_seconds":30,"query_timeout_seconds":120}` plus the same options | A polled job can run for two minutes while individual HTTP requests remain bounded to 30 seconds; raising every HTTP timeout to the full job duration delays detection of network failures. |

For both ClickZetta profiles, also merge these supported overrides:

```json
{
  "extra": {
    "http_connect_timeout": 5,
    "http_read_timeout": 30
  },
  "hints": {
    "sdk.job.polling.timeout": 20
  }
}
```

Five seconds bounds HTTP connection establishment; a 20-second service polling
wait leaves 10 seconds inside the 30-second HTTP read budget for transit and
response processing. This polling value is not the total job lifetime. Keep the
adapter's bounded retry defaults, and avoid also setting `sdk.job.timeout`,
`sdk.query.timeout.ms` or `querytimeout` in `hints`/`extra.hints`: those can
override the chosen query budget, with different units. Use positive integer
values for timeout hints.

BigQuery currently uses the same query value for foreground waiting and the
server job setting; a separate server-error delivery margin is not exposed.
Its server cancellation is best effort, as specified for
[`jobTimeoutMs`](https://docs.cloud.google.com/bigquery/docs/reference/rest/v2/Job#JobConfiguration).
Oracle's round-trip scope is specified in the
[`call_timeout` documentation](https://python-oracledb.readthedocs.io/en/stable/api_manual/connection.html#oracledb.Connection.call_timeout).
These differences are why the settings above do not promise a uniform total
operation deadline across adapters.

### Backend integration and tuning

Upgrade the adapter packages and shared bases, then update the backend lockfile
and deployed image. A backend that only passes `timeout_seconds` already gains
the documented fallback protection. To use these profiles, it must also forward
the optional fields to the adapter; this repository does not establish whether
the backend currently does so. Do not send MySQL-only fields to every adapter.

Allocate a database phase inside the backend's overall request deadline, leaving
time for other work such as model calls, serialization and response delivery.
Account for pool waiting, connection setup, metadata/context statements, query
execution, fetching and cleanup; these can consume separate budgets. Merely
making the outer timeout larger than `query_timeout_seconds` is insufficient.
An HTTP request timeout alone does not stop a synchronous database worker; the
adapter/driver bounds must remain finite as well.

Start with the interactive profile, then compare healthy p99 connection, query,
result-transfer and checkout durations with the configured limits. Increase a
limit when expected successful work needs it and the request budget permits it;
fix slow queries or pool saturation before globally lengthening waits. Use a
separate analytical connector/configuration for longer workloads, since these
policies are installed on physical connections. For example, a ten-minute report
can start with a 600-second query limit and a 660-second read/lease budget where
those controls exist, provided fetching and the whole transaction fit. Such work
should have a correspondingly sized job/request budget. Revalidate both server
deadline and stalled-network behavior after tuning; a successful `SELECT 1`
alone does not verify either.

## Relevant history

- `28371c0` added MySQL `connect_timeout_seconds`, `read_timeout_seconds`, and
  `write_timeout_seconds` with Aurora authentication. Explicit read/write values
  reached PyMySQL, but their `None` defaults left socket I/O unbounded. StarRocks,
  Doris, and TiDB did not expose or forward those overrides.
- `935c17f` fixed PostgreSQL-derived timeout/TLS arguments being overwritten after
  dialect translation. The regression tests correctly reach the final DBAPI
  boundary; the same testing approach is used here.
- `7e73607`, on `upstream/fix/classify-query-execution-timeout`, classifies server
  query deadlines as execution timeouts. It is **not an ancestor of `e959c5e`**.
  This fix incorporates that classification and adds regression coverage.

## MySQL, StarRocks, Doris, and TiDB

All four adapters now share the following positive, integer-second settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `timeout_seconds` | `30` | Pool checkout timeout, and fallback for each DBAPI connect/read/write timeout |
| `connect_timeout_seconds` | `None` | Override PyMySQL `connect_timeout`; `None` uses `timeout_seconds` |
| `read_timeout_seconds` | `None` | Override PyMySQL `read_timeout`; `None` uses `timeout_seconds` |
| `write_timeout_seconds` | `None` | Override PyMySQL `write_timeout`; `None` uses `timeout_seconds` |
| `query_timeout_seconds` | `None` | Opt in to a server-side SELECT execution deadline; `None` preserves the server's settings |

Zero and negative values are rejected. Existing workloads that legitimately wait
longer than 30 seconds for query results must increase the read timeout. Setting
`None` on a connect/read/write override inherits a finite timeout; it does not
disable the bound. The query override instead preserves the server policy.

These socket timeouts apply to connection setup, context switching, pool health
checks, queries, metadata, and `test_connection()`. PyMySQL closes sockets after
read/write timeouts (errors 2013/2006), and SQLAlchemy recognizes these as
disconnects and invalidates the connection. The operation is not replayed.
StarRocks also propagates a disconnected metadata query instead of starting its
compatibility fallback on a new connection. Its existing `SHOW` fallback remains
available for server errors on a usable connection. A later explicit operation
may obtain a fresh connection.

`test_connection()` uses the same error classification as queries. Socket timeouts
map to `DB_CONNECTION_TIMEOUT`; recognized server query deadlines map to
`DB_EXECUTION_TIMEOUT`.

### Server deadlines and cancellation

Socket I/O timeouts are **not a total operation deadline**. Several reads, context
statements, result transfer, and pool checkout can each consume time. A server
that keeps returning data can keep a query alive longer than one read timeout.
Closing a timed-out socket also does not guarantee immediate server cancellation.

For a server-enforced SELECT deadline, configure `query_timeout_seconds` below the
read timeout, leaving time to receive the server's error:

```python
from datus_starrocks import StarRocksConnector

connector = StarRocksConnector({
    "host": "starrocks.example",
    "username": "reader",
    "timeout_seconds": 30,
    "connect_timeout_seconds": 5,
    "query_timeout_seconds": 20,
})
```

The adapter initializes every physical connection, including replacement
connections, using the engine's session variable:

| Adapter | Session setup |
| --- | --- |
| MySQL | `SET SESSION max_execution_time = <seconds * 1000>` |
| TiDB | `SET SESSION max_execution_time = <seconds * 1000>` |
| StarRocks | `SET query_timeout = <seconds>` |
| Doris | `SET query_timeout = <seconds>` |

This option is explicit because engine/version support differs. An unsupported
setting fails connection setup instead of silently dropping the requested
deadline. Session settings can still be changed by application SQL or overridden
by engine-specific hints; this is a default execution policy, not a security limit.
MySQL's variable covers read-only SELECTs, and current TiDB versions also scope
it to SELECT. StarRocks has separate `insert_timeout` semantics for DML in newer
versions; Doris also has separate INSERT timeout rules. This option does not
promise a deadline for every DDL/DML statement or asynchronous job. No extra
`KILL QUERY` connection or automatic statement retry is introduced.

Primary references: [PyMySQL connection parameters](https://pymysql.readthedocs.io/en/latest/modules/connections.html),
[MySQL max_execution_time](https://dev.mysql.com/doc/refman/8.0/en/server-system-variables.html#sysvar_max_execution_time),
[TiDB system variables](https://docs.pingcap.com/tidbcloud/system-variables/#max_execution_time),
[StarRocks system variables](https://docs.starrocks.io/docs/sql-reference/System_variable/),
[Doris session variables](https://doris.apache.org/docs/4.x/sql-manual/basic-element/session-variables/).

## Other adapters: fixes and source audit

The same configuration gap was repaired at each driver's boundary. All timeout
values below must be positive. Optional overrides inherit `timeout_seconds` when
unset. Pool checkout, transport inactivity, individual SDK calls, and whole query
execution are different budgets; none of these adapters promises a single wall
clock limit spanning DNS, identity-provider calls, retries, pool checkout, query
execution, and all result pages.

| Adapter(s) | Implemented bounds |
| --- | --- |
| PostgreSQL, Greenplum, Hologres, DWS, GaussDB | Keep native connect timeout; add a connection-local socket deadline and `SET statement_timeout`. `read_timeout_seconds` overrides the checkout deadline, and `query_timeout_seconds` overrides the server statement limit. The default for both is `timeout_seconds` |
| Hive, Spark | Construct a fresh Thrift transport with `TSocket.setTimeout(timeout_seconds * 1000)` for every physical connection, preserving NOSASL, NONE, LDAP/CUSTOM and Kerberos. Send `queryTimeout` on ExecuteStatement requests, defaulting to `timeout_seconds`; override with `query_timeout_seconds` |
| Oracle | Pass `tcp_connect_timeout=timeout_seconds`, disable automatic connection retries, and set each connection's millisecond `call_timeout` before dialect initialization. `call_timeout_seconds` overrides the call budget. Timeout connections are invalidated |
| ClickHouse | Pass HTTP `timeout=timeout_seconds` and server `max_execution_time=query_timeout_seconds or timeout_seconds` through the actual driver settings; invalidate the DBAPI connection after native or wrapped HTTP/socket I/O errors |
| Trino | Pass `request_timeout=timeout_seconds`, `max_attempts=1`, and session `query_max_run_time=<query_timeout_seconds or timeout_seconds>s`; invalidate HTTP/socket failures even when wrapped by the native driver |
| BigQuery | Supply an owned SDK client with finite REST request/retry and credential-refresh timeouts. Bound foreground `query_and_wait` API/wait budgets, disable job replay, and configure `job_timeout_ms`. Retain service-account/ADC authentication, BigQuery/Cloud/Drive scopes, billing project and per-request dataset context. Close SDK sessions when engines are disposed |
| ClickZetta | Forward `http_connect_timeout`/`http_read_timeout`, default job/polling budgets and bounded SDK retry hints. Forward new timeout fields through the registry factory for dict and model configs. Extra hints retain default bounds; timeout hints are normalized using the SDK's integer conversion and rejected if the effective integer is nonpositive, nonfinite, boolean or otherwise invalid |

For PostgreSQL-family drivers, libpq does not provide a read-timeout keyword.
The adapter duplicates the connection socket and arms a cancellable timer around
connection initialization and every pool checkout (including result fetching and
reset). Expiry shuts down that socket; SQLAlchemy invalidates it. A separate
bounded pre-ping protects reused connections. The duplicate descriptor prevents
an expired timer from shutting down an unrelated connection after descriptor
reuse. This uses neither a global psycopg wait callback nor a background worker
executing SQL. **The checkout deadline is a total lease budget, not a timer reset
by each received packet.** Increase `read_timeout_seconds` for long transactions
or result transfers, and leave it above the desired server statement deadline
when the server's cancellation error should arrive first.

Oracle `call_timeout` applies to individual database round trips, rather than a
whole multi-round-trip statement. Hive/Spark server enforcement depends on the
Thrift server's support for `queryTimeout`. BigQuery job deadlines are best effort
server settings; API and foreground wait limits are separate. ClickZetta hints
are SDK/service policies, while HTTP limits also cover metadata requests. Server
settings are reapplied on new physical connections or owned client creation.
Transport failure does not automatically replay application SQL.

The remaining adapters already have explicit transport controls in this source
revision and did not have the same missing configuration propagation:

| Adapter | Existing controls / audit boundary |
| --- | --- |
| Snowflake | Explicit `login_timeout`, `network_timeout`, and `socket_timeout` |
| Redshift | Explicit native-driver `timeout`; IAM/provider behavior is separate |
| HANA | Positive millisecond `connectTimeout` and `communicationTimeout` |
| Athena | SDK connect/read timeouts, query/result wait limits and bounded cancellation polling |
| MaxCompute | Connector-local REST connect/read tuple and `wait_for_success` timeout; not a certification of every SDK-internal download path |

### ClickZetta SDK upgrade

The legacy `clickzetta-connector-python` distribution and the modern
`clickzetta-connector` distribution install overlapping module paths. The legacy
client ignores the required HTTP timeout options. The adapter now requires
`clickzetta-connector>=1.0.31` and `clickzetta-zettapark-python>=0.1.16`, without the
legacy dependency. Environments that already contain both distributions must
remove the legacy distribution and reinstall the modern one to restore shared
files; recreating the environment from the updated lockfile also avoids the
conflict. Tests instantiate the real modern SDK client and inspect its effective
HTTP timeout tuple.

## Regression coverage and real database validation

Unit tests reach the final DBAPI/SDK boundary, keeping URI construction and
dialect translation active. MySQL and StarRocks additionally use real PyMySQL and
SQLAlchemy against `datus_mysql.testing.StallingMySQLServer`, covering handshake,
queries, metadata, probes, invalidation, socket closure, no automatic replay and
recovery, including external StarRocks catalogs. Its independent safety deadline
prevents a regression from hanging the suite. Every MySQL-family adapter tests
session setup on replacement connections and unsupported server settings.

Live integration tests use a real vendor database behind
`datus_sqlalchemy.testing.BlackholeProxy`, authenticate normally, then discard
server replies for queries, metadata and probes. The proxy has an independent
safety deadline; tests require the adapter to return sooner and recover on the
next explicit request. Separate expensive queries validate actual server
cancellation on MySQL, PostgreSQL, Greenplum, StarRocks, Doris, ClickHouse, Trino
and Spark; Oracle uses a real sleeping PL/SQL call to exercise `call_timeout`.
TiDB and Hive runs verify transport timeouts, not their server cancellation
semantics. The testing standard now requires this test category.

Local validation on 2026-10-22 used disposable Docker databases, with test-owned
schemas/data and teardown. Counts below are distinct successful cases per engine,
including added timeout cases; repeated targeted runs are not counted twice.

| Real engine | Version | Passed | Skipped | Scope / skips |
| --- | --- | ---: | ---: | --- |
| MySQL | 8.0.46 | 36 | 13 | Full integration suite; Aurora/TLS profiles unavailable |
| PostgreSQL | 16 | 42 | 13 | Full suite and separated server/socket deadlines; Aurora/TLS profiles unavailable |
| StarRocks | 3.5.6 | 49 | 6 | Full local suite; external catalog fixtures unavailable in this run |
| Doris | 4.1.3 | 49 | 0 | Full suite, real Hive metastore catalog, server deadline and fault proxy |
| TiDB | 8.5 | 41 | 0 | Full suite and fault proxy |
| Greenplum | 6.27.1 | 43 | 0 | Full suite plus four timeout tests |
| Oracle Free | 23 | 39 | 14 | Full available suite and call deadline; optional HR sample schema absent |
| ClickHouse | 24.8.14 | 36 | 7 | Full local suite and server deadline; HTTPS profile unavailable |
| Trino | 479 | 31 | 7 | Full local suite plus server deadline; HTTPS profile unavailable |
| Hive | 4.0.1 | 28 | 39 | Native Hive suite and fault proxy; Inceptor/LDAP/Kerberos endpoints unavailable |
| Spark | 3.5 | 30 | 0 | Full suite plus server cancellation |

Total: **424 distinct integration cases passed on 11 real engines**. Skipped
profiles are explicitly excluded from that claim. Across 18 separately invoked
core/adapter unit suites, **2,111 tests passed** in the initial validation.
The PR coverage follow-up adds 45 database-free regression cases for the fault
proxy, Thrift authentication/query deadlines, PostgreSQL socket lifecycle and
pre-ping cleanup, Oracle connection initialization, SDK cleanup and probe error
classification. That follow-up passed **2,670 unit tests across the full 23-package workspace**
and reached **97.61% unit-only diff coverage**. The subsequent review fixes add
83 further regression cases; **2,753 unit tests pass** on the final implementation,
with **97.26% unit-only diff coverage (640/658 changed executable lines)**,
confirmed after committing all new files and by the PR coverage workflow.
Live integration coverage is not included in the PR's diff coverage figure.
Shared helpers are tested in their owning package so per-package collection
includes those paths. Formatting/lint and
wheel/sdist checks are recorded separately below.

GaussDB/openGauss `7.0.0-RC2.B015` could not start on the local ARM64 Docker host:
multiple fresh initializations aborted with SIGABRT/SIGTRAP (not OOM), including
reduced-memory settings. Logs include cgroup configuration parsing failures, but
the root cause is not established. This is **not a passing GaussDB native test**.
Its psycopg2 transport path separately passed four deadline/fault tests against
real PostgreSQL; that is protocol-path coverage, not vendor compatibility proof.
The GaussDB pg8000 path cannot use that surrogate because its vendor handshake
rejects a PostgreSQL protocol-negotiation response. New GaussDB integration tests
remain available for a working vendor instance.

No BigQuery, ClickZetta, DWS or Hologres real-service credentials were available;
their final driver/client configuration is unit tested. No cloud-service live
result is claimed. Existing Snowflake credentials were not used for this local
validation request.

### Review follow-up validation

The duplicated live fault tests now call one shared
`assert_stalled_response_timeout` contract. It checks one application statement,
one DBAPI execution, no automatic physical-connect attempts, SQLAlchemy
invalidation/close events, physical TCP closure before the proxy safety deadline,
and exactly one fresh DBAPI connection for subsequent explicit recovery.
Plaintext protocols additionally count a unique SQL marker across complete TCP
streams, detecting internal HTTP retries without confusing packet fragmentation
with requests. TLS remains enabled where negotiated; encrypted operations are
counted at the DBAPI boundary and all old TCP connections must close. Faults in
plaintext protocols activate only after the marked SQL arrives, allowing libpq's
implicit BEGIN to complete first.

A deliberately retrying connector is rejected by the database-free contract
regression. The PostgreSQL family also checks timeout wiring through real
connectors and dialects for default/overridden connect, read and statement limits,
both base and per-database engines, and replacement connection callbacks. GaussDB
covers all three dialects; its native driver boundary is stubbed when testing
configuration without a vendor runtime.

The strengthened tests were rerun on the same **11 real engine types**, with
**42 targeted timeout cases passing** (33 fault-injection cases plus nine server
or call-deadline cases). These are a targeted follow-up to the earlier 424-case
integration run, not another full integration sweep. They exposed HTTP connection
reuse after timeout in ClickHouse and Trino; both now invalidate native or wrapped
socket errors. No native GaussDB or credential-dependent cloud-service validation
has been added to the claims above.

ClickZetta rejects fractional hints that the SDK would truncate to zero, plus
NaN, infinity, booleans and malformed numeric strings. Real SDK job-request tests
verify the effective positive `jobTimeoutMs` for seconds and millisecond aliases.

### Aurora checkout ordering follow-up

Review found that a reused Aurora PostgreSQL connection could run its writer
validator after pre-ping but before the checkout socket lease was armed. Its
`pg_is_in_recovery()` probe or subsequent rollback could then wait indefinitely
for a lost reply. The lease listener now runs before existing checkout
validators, as it already did for connection initialization.

Two database-free regressions use the production writer validator, actual
SQLAlchemy pool/event dispatch and socket-backed DBAPI calls. They separately
stall the probe and rollback after a successful reuse pre-ping. Before the fix,
both reached the independent three-second safety timeout; after it, the
one-second lease interrupts the read, invalidates/closes the connection, releases
the duplicated descriptor and permits recovery on a later explicit request.

The follow-up passed **704 unit tests** across PostgreSQL, Greenplum, GaussDB,
DWS, Hologres and SQLAlchemy, plus **six real PostgreSQL 16.15 timeout tests**.
The latter include two new cases exercising the production Aurora writer
validator through a fault proxy on both base and per-database engines. This is
local PostgreSQL protocol/lifecycle coverage, not a live Aurora service test.
The testing standard now explicitly requires composed checkout-validator
coverage rather than testing pre-ping and validation only in isolation.

### Verified-TLS fault-proxy follow-up

Merge-queue run `39924728989` exposed 14 fault-test failures across MySQL,
PostgreSQL, ClickHouse and Trino: the local proxy settings used `127.0.0.1`,
while the verified server certificates identify `localhost`. The shared contract
and PostgreSQL writer-validator cases now use that DNS name, retaining CA and
hostname verification. A database-free HTTPS protocol peer exercises the real
ClickHouse driver through the full shared contract for queries, metadata and
connection probes; a negative case verifies that the IP is still rejected.

Disposable CAs now include explicit critical CA constraints and signing key usage,
which strict X.509 verification requires (also exercised by Python 3.13). The
certificate regression checks strict server verification plus wrong-CA and
wrong-hostname rejection without weakening the generated server identity.

Local validation on 2026-10-23 passed the complete verified-TLS suites on four
real databases: **PostgreSQL 47, MySQL 39, ClickHouse 43 and Trino 38 cases**,
**167 in total**, with no skips. This includes certificate rejection, timeout
invalidation/physical closure, no replay and subsequent explicit recovery.
All invocation-owned Compose projects and volumes were removed. Separately,
**637 unit tests** passed across SQLAlchemy, PostgreSQL, MySQL, ClickHouse and
Trino, including four new TLS fault-proxy regressions.

## Packaging and rollout

Prepared patch versions (not published):

| Package | Version |
| --- | --- |
| `datus-sqlalchemy` | `0.1.11` |
| `datus-mysql` | `0.1.10` |
| `datus-postgresql` | `0.1.11` |
| `datus-starrocks` | `0.1.12` |
| `datus-doris` | `0.1.3` |
| `datus-tidb` | `0.1.1` |
| `datus-greenplum` | `0.1.5` |
| `datus-gaussdb` | `0.1.4` |
| `datus-dws` | `0.1.2` |
| `datus-hologres` | `0.1.1` |
| `datus-hive` | `0.1.4` |
| `datus-spark` | `0.1.5` |
| `datus-oracle` | `0.1.1` |
| `datus-clickhouse` | `0.1.5` |
| `datus-trino` | `0.1.5` |
| `datus-bigquery` | `0.1.1` |
| `datus-clickzetta` | `0.1.6` |

All 17 changed packages build as wheels and source distributions. Isolated
imports from extracted wheels, packaged skill-resource byte checks and workspace
dependency lower bounds are validated. Derived adapters require the new shared
base versions, so an older installed base cannot silently satisfy the dependency.
Publish SQLAlchemy first, then MySQL/PostgreSQL, then their derived adapters and
the other changed packages; update the backend lockfile/image afterwards.
Publishing and backend deployment have not been performed.

Existing long-running workloads may need larger positive timeouts now that
previously ignored settings take effect. MySQL-family server deadlines remain
opt-in; other repaired adapters default their described query/call budgets to
`timeout_seconds`. The companion
[event-loop isolation issue](https://github.com/Datus-ai/Datus-agent/issues/1529)
is still relevant while synchronous database calls wait.
