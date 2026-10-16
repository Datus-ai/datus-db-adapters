# SAP HANA adapter

Native SAP HANA access through SAP's official Python `hdbcli` client. No Java,
SQLAlchemy HANA dialect, or local HANA server is required on the client machine.

## Installation and configuration

```bash
pip install datus-hana
```

This package requires db-core 0.1.8 and the companion Agent changes that consume
native SQL analysis. Merge/deploy those changes before enabling HANA tools.
Installing the SDK alone into an older Agent does not provide safe HANA scope
analysis. Restart Datus after upgrading db-core in an existing environment;
hot-installing a new wheel does not replace already imported Python classes.

```yaml
agent:
  services:
    datasources:
      hana_example:
        type: hana
        host: hana.example.internal
        port: 39041
        username: DATUS_TEST
        password: ${HANA_PASSWORD}
        database: HXE
        schema: DATUS_TEST
        encrypt: true
        ssl_validate_certificate: true
        ssl_trust_store: /etc/certs/hana-ca.pem
        timeout_seconds: 30
        communication_timeout_seconds: 60
        max_result_rows: 10000
        max_result_bytes: 16777216
```

`database` selects a tenant at connection time; it is not part of SQL object
names. Configure the actual tenant SQL endpoint (HANA Cloud commonly uses port
443). Only `schema.table` is an SQL namespace. Names returned by metadata retain
their actual case. SQL folds unquoted identifiers to uppercase; explicitly quote
mixed-case names. The connector quotes names exactly as supplied by metadata.

For an existing HANA secure user store, configure `userkey: DATUS_TEST` instead
of username/password. The key must be available to the OS user running Python.
Omit `host` to use the endpoint from that user store. `hdbuserstore` is an
additional SAP client utility; installing the Python wheel alone does not
provision a user store. `schema` and TLS settings still apply.

TLS and certificate verification are enabled by default. Set a trust store and,
when needed, `ssl_host_name_in_certificate` for the expected certificate name.
For an explicitly isolated Express test without TLS, set `encrypt: false`.
Connection and communication timeouts are milliseconds in SQLDBC and are
converted from the seconds specified above. Communication timeout is a network
request bound, not a guarantee that server-side SQL has been cancelled.

## Execution and analysis

```python
from datus_hana import HanaConfig, HanaConnector, register

register()
config = HanaConfig(host="localhost", port=39041, username="DATUS_TEST",
                    password="provided-at-runtime", schema="DATUS_TEST")
with HanaConnector(config) as connector:
    result = connector.execute_query(
        'SELECT * FROM "DATUS_TEST"."ORDERS" WHERE "ID" = ?',
        result_format="list", parameters=(42,),
    )
    if not result.success:
        raise RuntimeError(result.error)
```

SQL and DB-API parameters are passed unchanged to the server. Each operation
owns a connection and cursor; commit, rollback, and cleanup are explicit.
`execute_queries()` groups DML in one transaction. HANA DDL may commit implicitly,
so a batch containing DDL is not promised to be atomic. There is no automatic
replay of writes after a connection error. List, CSV, pandas, and Arrow outputs
are supported. Exceeding the row/byte budget fails explicitly, with no silent
truncation; duplicate result names require aliases. `close()` is idempotent.

Metadata comes from SAP system views, independently of SQL parsing. Table DDL
returned for discovery describes columns only; constraints, defaults, physical
layout, and lossless migration are not claimed. Migration target capabilities
are explicitly unsupported.

Local analysis is a separate, limited feature for ordinary SELECT, JOIN, CTE,
aggregation, window, set operations, and LIMIT. It uses an explicitly selected
compatible parser, never regenerates execution SQL, and preserves quoted names.
SQLScript, table functions, Calculation View parameters, opaque statements,
multi-statement input, SELECT INTO, and locking queries are not accepted for
local read/scope analysis. The SDK can still execute server-supported SQL via
its direct execution methods. Datus tools must use the optional `sql_analyzer`
hook and reject unsupported analysis before dispatch; see the companion Agent
change. Full formatting, policy rewriting, transpilation and lineage are not
declared for HANA. A generic parser mapping does not imply HANA grammar support.

## Testing

Read `../docs/adapter-testing-standard.md`. Run suites separately by package:

```bash
cd datus-hana
uv run python -m pytest tests/unit -m "not integration"
uv run python -m pytest tests/integration -m integration
```

Integration configuration uses `HANA_HOST`, `HANA_PORT`, `HANA_USER`,
`HANA_PASSWORD`, optional `HANA_USERKEY`, `HANA_DATABASE`, `HANA_SCHEMA`,
`HANA_ENCRYPT`, `HANA_SSL_TRUST_STORE` and `HANA_SSL_HOST_NAME_IN_CERTIFICATE`.
Cloud/enterprise instances use the same test configuration. Missing credentials
skip an unconfigured developer suite; the CI readiness probe must fail when an
enabled target cannot authenticate. Provisioning failures after connection fail
the tests. Use a dedicated user/schema with permissions to create tables/views.
TLS cases require `HANA_ENCRYPT=true` and a valid trust configuration. Set
`HANA_TEST_USERKEY` to an additional pre-provisioned key for the dedicated test
user to run the user-store round trip. Password tests deliberately make one
failed authentication attempt; use the dedicated disposable test identity.

On Linux x86_64, the supplied Compose file uses the official HANA Express image.
The host needs the SAP-documented kernel settings and enough memory/disk for
startup, image unpacking, and data. It is not an ARM Mac server image.

```bash
# On a prepared Linux x86_64 test host; credentials are injected at runtime.
export DATUS_TEST_HANA_PASSWORD="${HANA_PASSWORD}"
export HANA_HOST=127.0.0.1 HANA_PORT=23941
export HANA_USER=DATUS_TEST HANA_SCHEMA=DATUS_TEST HANA_ENCRYPT=false
sudo --preserve-env=DATUS_TEST_HANA_PASSWORD,HANA_MEMORY_LIMIT_GIB,HANA_MIN_FREE_DISK_GIB \
  python3 scripts/prepare_express.py
docker compose up -d
# Authenticate, create the test schema/user, and verify SELECT 1 FROM DUMMY.
uv run python scripts/wait_for_hana.py
uv run python scripts/init_tpch_data.py --schema DATUS_TEST --drop
docker compose down -v
sudo python3 scripts/prepare_express.py --cleanup
```

The integration target remains disabled until the automated job has dedicated
storage, credentials, a selected sysctl profile, and TLS/user-store fixtures.
Express readiness and resource usage have been validated in manual trials.
Initial rollout must serialize HANA with Oracle and other heavy CI databases.
Each run needs its own schema or exclusive scheduling; cleanup only removes that
run's HANA resources.

The minimal functional-test profile uses a container limit of 8 GiB RAM
(`HANA_MEMORY_LIMIT_GIB`, swap disabled) and 2 CPUs (`HANA_CPU_LIMIT`). SAP
[documents 8 GB minimum and 12 GB recommended for server-only](https://developers.sap.com/tutorials/hxe-database-server/).
`prepare_express.py --check-only` requires the configured memory limit plus 2 GiB
available host RAM, and 12 GiB free disk (`HANA_MIN_FREE_DISK_GIB`). The disk check
is an adjustable first-run budget, not an SAP minimum, measured footprint, or
volume quota. The container memory limit is a cap, not preallocated RAM. Measure
startup/test peaks, including image unpacking and data/log files; increase the
limit only if a measured OOM or allocation failure requires it. If HANA's own
allocation policy needs tuning, use the documented `global_allocation_limit`
in addition to the container cap. Remove only this run's state after stopping it.

The documented host kernel parameters are checked separately. A lower
`vm.max_map_count` limits memory-map areas per process; a lower `fs.aio-max-nr`
limits system-wide Linux AIO reservations. They are limits, not preallocated
RAM. A small workload may fit existing limits, but the image's startup check can
still reject them. The image documents `--dont-check-system` to skip its check;
that does not raise the actual limits or prove runtime compatibility. The
default test configuration keeps the checks and never changes host sysctls.
The prepared bootstrap file is private and owned by the image user (12000:79).
The CI hook removes it after the run.

To explicitly trial the existing host sysctls, pass `--skip-sysctl-check` to
`prepare_express.py` and use both Compose files for startup and cleanup:

```bash
sudo --preserve-env=DATUS_TEST_HANA_PASSWORD,HANA_MEMORY_LIMIT_GIB,HANA_MIN_FREE_DISK_GIB \
  python3 scripts/prepare_express.py --skip-sysctl-check
docker compose -f docker-compose.yml -f docker-compose.skip-sysctl.yml up -d
# Run readiness and integration tests using the environment documented above.
docker compose -f docker-compose.yml -f docker-compose.skip-sysctl.yml down -v
# Unmount a separately prepared filesystem before removing its mount directory.
sudo python3 scripts/prepare_express.py --cleanup
```

This opt-in bypasses the project and image sysctl checks, without changing host
limits, and clears container sysctl overrides (Compose >= 2.24). Resource checks
still apply; startup/runtime success must be measured. The ephemeral test keeps
server executables in the image layer instead of copying `/hana/shared` into a
named volume on every cold start.
The image writes data, log, trace and system state beneath `/hana/mounts`, which
is the private `.express` bind mount. Stop Compose before calling `--cleanup`;
that removes this checkout's complete disposable test state. Cleanup refuses a
symlink or mounted directory. A small separate filesystem for the bind mount
also avoids inheriting a nearly-full host partition's utilization: HANA's
default `log_disk_usage_limit=95` can pause initialization even when several
GiB remain free on a large shared filesystem.

The 2026-10-16 isolated CI trial passed all 20 integration cases with 8 GiB RAM,
2 CPUs and an 8 GiB sparse ext4 data filesystem. Peak cgroup memory was
7.459 GiB without OOM; data filesystem usage was 3.639 GiB (the sparse image
occupied 3.735 GiB physically), plus a 4.182 GiB server image. The sysctls
remained `vm.max_map_count=1048576` and `fs.aio-max-nr=65536`. First image pull
used a 12 GiB preflight budget; later runs with the cached image used 8 GiB.
These measurements cover this fixed image and the small functional fixtures.
TLS used a disposable CA-signed server certificate with DNS SAN `hana` and
certificate validation enabled. User-store setup reused the image's
`/usr/sap/HXE/HDB90/exe/hdbuserstore` and isolated `HDB_USE_STORE_PATH`.

The companion Agent's `tests/integration/agent/test_hana_chat_agentic.py` also
passed two real CLI/DeepSeek/HANA end-to-end cases (three conversation turns).
They verify table discovery, column metadata, JOIN/aggregation, exact answer
amounts against an independent SDK oracle, follow-up filter retention, Chinese
results, and startup metadata indexing. Only terminal input is replaced. Each
case uses an isolated Datus home/project and randomly named disposable tables.
The Agent must initialize schema-only targets even when `database` is empty,
and table/view metadata must match its storage schema. Eleven affected metadata,
shared-contract, and TPC-H wire tests passed after those integration fixes.

To run the Agent cases, install the local core/adapter and companion Agent
packages, configure the same `HANA_*` variables above, and supply a real model key
and the ordinary nightly metadata embedding cache. Run from the Agent checkout:

```bash
HANA_REQUIRE_AGENT_E2E=1 .venv/bin/python -m pytest \
  tests/integration/agent/test_hana_chat_agentic.py -m product_e2e
```

`HANA_AGENT_CONFIG` defaults to `tests/conf/agent.yml`; `HANA_AGENT_MODEL` can
select a configured model alias. These cases cover the common query/answer
workflow, not policy SQL rewriting or static lineage. An existing but untrusted
CA, large LOBs and result-budget boundaries still need live boundary validation.

Real HANA Cloud/commercial-version validation is pending. Offline tests do not
establish Cloud/enterprise compatibility or production certificate policies.

References: [Python client](https://pypi.org/project/hdbcli/),
[connection properties](https://help.sap.com/docs/hana-cloud-database/f1b440ded6144a54ada97ff95dac7adf/ee592e89dcce4480a99571a4ae7a702f.html),
[Express image](https://hub.docker.com/r/saplabs/hanaexpress).
