# Integration Tests

Integration tests require a real Hive Server2 endpoint.

## Quick Start

```bash
# Start Hive stack
docker-compose up -d

# Run integration tests
uv run pytest tests/integration/ -m integration -v

# Stop Hive stack
docker-compose down
```

## Setup

### Docker (Recommended)

Hive services are pre-configured in `docker-compose.yml`:
- HiveServer2: `localhost:10000`
- Metastore: `localhost:9083`
- Database: `default`

### Manual Setup (Alternative)

Set environment variables:
```bash
export HIVE_HOST=localhost
export HIVE_PORT=10000
export HIVE_USERNAME=hive
export HIVE_PASSWORD=
export HIVE_DATABASE=default
# Optional auth method (e.g., CUSTOM, LDAP, NONE)
export HIVE_AUTH=
# Optional JSON map of configuration values
export HIVE_CONFIGURATION_JSON='{"spark.app.name":"datus_hive_test"}'
```

## Notes

- Tests create and drop temporary tables/views.
- If INSERT is not supported, the sample-row test will be skipped.

## Troubleshooting

**Port 10000 in use?**
```bash
# Change port in docker-compose.yml
ports:
  - "10001:10000"
# Then set:
export HIVE_PORT=10001
```

**Clean slate?**
```bash
docker-compose down -v
```

## Manual Inceptor tests

See [the local environment guide](../../local/inceptor/README.md) for Compose,
initialization, NONE and Kerberos readiness, and the exact pytest commands. Set
`INCEPTOR_TEST=1` to opt into `inceptor/`. Once selected, connection and fixture
errors fail the run; they never become availability skips. This target is not
enabled in CI.

## Dedicated Hive/Inceptor compatibility contracts

The versioned data in `tests/fixtures/hs2` is an explicit snapshot of
`osi-engine/tests/fixtures/hs2`; `hs2.lock.json` records its content hash and
source base commit. Update it with `osi-engine/scripts/sync_hs2_fixtures.py`.
The shared Dosi metric corpus is independent of these tests.

For a local Hive 4.0.1 service, run from the repository root:

```bash
docker compose -p hs2-contract -f datus-hive/docker-compose.yml \
  -f datus-hive/tests/integration/hs2/compose.yaml up -d
```

The overlay uses a shared writable warehouse and local MapReduce, with tiny
map-join tasks running in process. Verify ORC create/insert/read
before testing. Remove the owned stack and volume with the same command
ending in `down -v` after testing.

Install the workspace dev dependencies (including PyYAML), then select one of
`hive_utc`, `hive_non_utc`, `inceptor_ce_v1`, `inceptor_ce_v2`, or
`inceptor_ce_v2_non_utc`. Supply an external Dosi datasource YAML using
`type: hive` and the matching `server_type`. For example:

```bash
HS2_TEST_PROFILE=inceptor_ce_v2 \
DOSI_TEST_HIVE_CONNECTIONS=/path/to/private/connections.yaml \
uv run pytest tests/integration/hs2 -m integration -v
```

`DOSI_TEST_HIVE_CONNECTION` selects the datasource (default `hive`);
`HS2_TEST_REPORT_DIR` selects the JSON report directory. Kerberos uses an
externally acquired ticket. Each run verifies the server version and settings,
creates uniquely named databases, and cleans up partially loaded fixtures.
Once selected, setup, query and cleanup failures fail the run. The tests are
not collected without `HS2_TEST_PROFILE`. Run Hive and Inceptor sequentially on
memory-constrained hosts. The Inceptor CI target remains disabled until a
scoped endpoint and credentials are available.

Collect Rust native SQL, Rust metrics, adapter and Agent reports in a fresh
report directory. From the Dosi checkout, `python scripts/hs2_report.py
--reports /path/to/reports` requires every profile and execution path to pass.
Use repeated `--profile` options to gate a selected subset. The gate rejects
stale fixture hashes, missing cases/formats, failed or skipped checks and
cleanup errors; it never updates expected results.
