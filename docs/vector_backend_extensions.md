# Vector Backend Extensions in `datus-db-adapters`

This document explains the current PostgreSQL (`pgvector`) vector support and provides an implementation path for adding vector capabilities to other adapters.

## Current Status

| Adapter | Relational Connector | Vector Backend |
| --- | --- | --- |
| `datus-postgresql` | Yes | Yes (`pgvector`) |
| `datus-mysql` | Yes | Not implemented |
| `datus-starrocks` | Yes | Not implemented |
| `datus-snowflake` | Yes | Not implemented |
| `datus-clickzetta` | Yes | Not implemented |
| `datus-redshift` | Yes | Not implemented |

## PostgreSQL + pgvector Setup

### 1. Install Adapter

```bash
pip install datus-postgresql
```

### 2. Enable `vector` Extension in Target Database

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

If your DB user cannot create extensions (common on managed services), run this through DBA/cloud-admin once per database.

### 3. Apply DDL (Manual/Offline)

`datus-postgresql` does not auto-create runtime tables for pgvector. Apply SQL manually:

- `datus-postgresql/datus_postgresql/ddl/all.sql`
- or `datus-postgresql/datus_postgresql/ddl/vector.sql` (vector tables only)

You can also regenerate SQL with your embedding dimension:

```bash
python - <<'PY'
from datus_postgresql.storage_ddl import render_all_ddl
print(render_all_ddl(vector_dim=384, schema="public"))
PY
```

### 4. Schema Compatibility Note

`ext_knowledge` requires an `id` column and namespace-scoped id uniqueness for upsert semantics:

- `id TEXT`
- `UNIQUE (namespace, id)`

This aligns PostgreSQL tables with Datus storage behavior for external knowledge dedup/upsert.

## Runtime Safety Checks

`PgVectorBackend` performs an early extension check during initialization:

- verifies `pg_extension` has `vector`
- fails fast with an actionable error if missing

This avoids late query failures such as `type "vector" does not exist`.

## How to Add Vector Support for Another Adapter

1. Implement a `VectorBackend` class in the adapter package.
2. Register it in adapter `register()` via `register_vector_backend("<name>", BackendClass)`.
3. Define a DDL strategy (manual first is recommended).
4. Ensure namespace filtering and conflict/upsert rules are explicit.
5. Add tests:
   - filter compilation and namespace scoping
   - add/upsert/search/hybrid behavior
   - schema compatibility checks
6. Document install/config/DDL/verification/troubleshooting in adapter README.

## PR Checklist

- [ ] Extension prerequisites are documented
- [ ] DDL files are updated/generated consistently
- [ ] Store schema fields match backend table schemas
- [ ] Upsert keys are namespace-safe
- [ ] Unit tests cover critical vector-path logic
- [ ] README links to this guide
