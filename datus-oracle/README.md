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

# Integration tests (requires a running Oracle database)
cd datus-oracle && python -m pytest tests/integration/ -v
```
