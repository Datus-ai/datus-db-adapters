# datus-oracle

Oracle database adapter for [Datus](https://github.com/Datus-ai/datus-agent).

Generates Oracle Database 19c-compatible SQL and connects via [python-oracledb](https://python-oracledb.readthedocs.io/) Thin mode (no Oracle Client required, supports Oracle Database 12.1+). Integration CI runs against Oracle AI Database 26ai Free (23.26.3).

## Timeouts

`timeout_seconds` must be positive and now bounds driver I/O as well as pool
checkout where applicable. Long operations may need a larger value. Server
execution deadlines and supported overrides are described in the shared
[timeout policy and real-database validation](../docs/adapter-timeouts.md).
A socket/RPC timeout is distinct from an overall operation deadline.

## Installation

```bash
pip install datus-oracle
```

## Configuration

```yaml
services:
  datasources:
    oracle_demo:
      type: oracle
      host: 127.0.0.1
      port: 1521
      username: datus_test
      password: ${ORACLE_PASSWORD}
      service_name: FREEPDB1
      schema: DATUS_TEST
      default: true
```

Exactly one connection target is required: `service_name` (recommended, addresses a PDB/service), `sid` (legacy environments) or `dsn` (TNS alias). `database` is accepted as a compatibility alias for `service_name`.

The service/PDB is a connection target only — SQL object identifiers are `SCHEMA.TABLE`. The default schema is the configured `schema`, otherwise the connecting user's schema.

## Notes

- Row limiting uses `FETCH FIRST n ROWS ONLY` (Oracle has no `LIMIT` clause).
- Identifiers are quoted upper-cased (`"ORDERS"`), equivalent to unquoted names and safe for reserved words.
- Booleans are stored as `NUMBER(1)` with values 1/0 (Oracle 19c has no SQL BOOLEAN column type).
- Metadata uses `ALL_*` dictionary views only — no `DBA_*` access or `SELECT_CATALOG_ROLE` required.
- Bulk writes use bound parameters with `executemany` (Oracle 19c does not support multi-row `INSERT ... VALUES`).
- The package exposes `db-oracle-sql` through the `datus.skills` entry point. Datus Agent 0.3.9 receives the same SKILL.md body through its `sql_generation_notes` compatibility hook.

## Testing

```bash
# Unit tests (no database needed)
cd datus-oracle && python -m pytest tests/unit/ -v

# Start the local integration database. The DATUS_TEST_* pair provisions the
# container and is required; choose values not reused outside this disposable
# environment. ORACLE_PASSWORD is what the tests connect with, so it has to
# match DATUS_TEST_ORACLE_PASSWORD.
export DATUS_TEST_ORACLE_SYS_PASSWORD='<strong-sys-password>'
export DATUS_TEST_ORACLE_PASSWORD='<app-password-starting-with-a-letter>'
export ORACLE_PASSWORD="$DATUS_TEST_ORACLE_PASSWORD"
docker compose up -d

# Integration tests
cd datus-oracle && python -m pytest tests/integration/ -v
```

The compose environment pins `gvenzl/oracle-free:23.26.3-slim` by digest and
connects through `FREEPDB1`. The CI runner keys the preserved data volume by the
full image reference, so an image upgrade initializes a separate database with
matching timezone files. Keep `ORACLE_IMAGE` overrides pinned to an exact image,
and do not reuse an `ORACLE_DATA_VOLUME_KEY` across image versions. Direct Compose
runs should likewise select a new volume key when changing images.

Older volumes remain available for diagnosis or rollback; remove them explicitly
once they are no longer needed. `DATUS_RECREATE_VOLUMES=1` resets only the selected
CI database volume. The first database creation is slower than restarting an
initialized volume; the CI readiness probe allows up to 20 minutes by default.

### Sample schemas

The container also installs Oracle's official **HR** sample schema (7 tables,
216 rows) from `docker/sample-schemas/human_resources/`, driven by
`docker/init/02_install_hr_schema.sh`. `tests/integration/test_sample_schema_hr.py`
uses it to cover what the TPC-H fixtures cannot: foreign keys, the
`employees.manager_id` hierarchy (`CONNECT BY`), a view listed separately from
tables, and column comments.

The installation is idempotent and adds a couple of seconds to the first
container start. Set `ORACLE_SKIP_SAMPLE_SCHEMAS=1` to skip it — the HR tests
then skip as well. `ORACLE_HR_PASSWORD` overrides the HR account password,
which otherwise reuses the app user's.

The `sales_history` (SH) schema is deliberately **not** vendored: its data files
total ~91 MB, which does not belong in this repository or in a CI image build.
