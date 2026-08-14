# datus-oracle

Oracle database adapter for [Datus](https://github.com/Datus-ai/datus-agent).

Targets Oracle Database 19c; connects via [python-oracledb](https://python-oracledb.readthedocs.io/) Thin mode (no Oracle Client required, supports Oracle Database 12.1+).

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
      service_name: ORCLPDB1
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

# Start the local integration database. Both passwords are required; choose
# values that are not reused outside this disposable development environment.
# The default Oracle 19c Enterprise Edition image requires accepting its license
# in Oracle Container Registry and logging Docker in to that registry first.
docker login container-registry.oracle.com
export ORACLE_SYS_PASSWORD='<strong-sys-password>'
export ORACLE_PASSWORD='<app-password-starting-with-a-letter>'
docker compose up -d

# Integration tests
cd datus-oracle && python -m pytest tests/integration/ -v
```

The compose environment pins Oracle Database 19c `19.3.0.0`, uses
`ORCLCDB`/`ORCLPDB1`, and stores its data in a dedicated 19c volume. The first
database creation is substantially slower than restarting an initialized
volume; the CI readiness probe allows up to 20 minutes by default.

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
which otherwise reuses `ORACLE_PASSWORD`.

The `sales_history` (SH) schema is deliberately **not** vendored: its data files
total ~91 MB, which does not belong in this repository or in a CI image build.
