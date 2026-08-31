---
name: db-dws-sql
description: Generate, review, and understand Huawei Cloud GaussDB(DWS) SQL. Use for DWS queries, DDL, DML, table design, and SQL rewrites where PostgreSQL 9.2 compatibility, ORA/TD/MySQL compatibility-mode semantics, row/column storage, data distribution, partitioning, or materialized-view version limits matter.
---

# DWS SQL

Generate DWS-compatible SQL from metadata-provided object and column names. The baseline syntax is PostgreSQL 9.2 — DWS reports `server_version` as `9.2.4` — extended with DWS's own storage and distribution clauses. Do not assume PostgreSQL 9.3+ features are available; confirm against metadata or the target version first.

## Compatibility-mode semantics

Every database has a compatibility mode, readable from `pg_database.datcompatibility`: `ORA`, `TD` or `MySQL`. The mode changes expression semantics, not just formatting. The rules below are verified against ORA mode, which is the default for new clusters.

**These two silently produce wrong results — never ignore them:**

- **`/` is not integer division.** `7/2` yields `3.5` as `double precision`, where standard PostgreSQL yields integer `3`. When integer semantics are intended, write `floor(a/b)::int` or `div(a, b)` explicitly rather than relying on operand types.
- **The empty string is NULL.** `'' IS NULL` is true, and a stored `''` reads back as NULL. This happens at write time, so no query-side rewrite recovers it. Never use `col = ''` to test for an empty value — it matches nothing; use `col IS NULL`. Treat "empty string" and NULL as the same value when reasoning about the data.

Other verified ORA-mode differences from standard PostgreSQL:

- `'a' || NULL` yields `'a'`; concatenation absorbs NULL instead of propagating it. When PostgreSQL NULL-propagating semantics are required, test the operands explicitly.
- `DATE` is `timestamp(0) without time zone` and carries a time component; a `DATE` column reports as `timestamp without time zone` in metadata. Use `to_char(d, 'YYYY-MM-DD')` when a pure date string is wanted.
- `DATE - DATE` yields an `interval`, not an integer count of days. Use `EXTRACT(day FROM a - b)` for a numeric difference.
- `char(n)` pads on storage: `length()` returns `n`, while `=` ignores trailing blanks. Prefer `varchar(n)` unless fixed width is required.

Behaviour matching standard PostgreSQL and safe to rely on: trailing-blank sensitivity for `varchar` equality, `SUM` over an empty set returning NULL, and ISO week numbering.

TD and MySQL modes are **not verified**. When metadata reports either, state that the semantics above may not hold rather than assuming they carry over.

## Namespaces and identifiers

- Address objects as `schema.table`; use `database.schema.table` only when the metadata supplies a database. DWS has no catalog layer, so a four-part name is invalid.
- Fold unquoted identifiers to lowercase. Preserve mixed case, reserved words, and special characters with double quotes; do not use backticks or square brackets.
- Do not generate objects in `cstore`, `sys`, `pg_recyclebin`, `gs_logical_cluster`, `scheduler`, or any `dbms_*`, `utl_*`, `dbe_*` schema — these belong to DWS.

## Table design

Storage orientation and distribution are creation-time decisions that normally require recreating the table to change. Choose them from workload evidence; when evidence is absent, say so rather than guessing.

- `WITH (orientation=column)` for analytical scans over few columns of a wide table — the common default for a warehouse. `orientation=row` for point lookups and frequent single-row updates.
- `compression=low|middle|high` applies to column storage; higher compression trades CPU for I/O.
- `DISTRIBUTE BY HASH (col)` for large tables. Choose a high-cardinality column that appears in joins and grouping, so that joins can run co-located and data lands evenly. A low-cardinality key causes skew, and a skewed node bounds the whole query.
- `DISTRIBUTE BY REPLICATION` for small dimension tables that join against many facts — every node holds a full copy, removing redistribution at the cost of storage.
- `DISTRIBUTE BY ROUNDROBIN` when no column is a good hash key; it distributes evenly but cannot support co-located joins.
- Join keys that match the distribution keys of both sides avoid a redistribution step. When they do not match, DWS redistributes or broadcasts — correct, but the dominant cost in large joins.

Partitioning uses the Oracle-style range form:

```sql
PARTITION BY RANGE (dt) (
    PARTITION p2026 VALUES LESS THAN ('2027-01-01'),
    PARTITION pmax VALUES LESS THAN (MAXVALUE)
)
```

Partitioning and distribution are independent and compose: partitions prune by predicate, distribution spreads across nodes.

## DDL portability

`pg_get_tabledef()` returns the authoritative definition including `orientation`, `compression`, `DISTRIBUTE BY`, `TABLESPACE` and `TO GROUP`. Two of those clauses are properties of the cluster that produced them:

- `TO GROUP <node_group>` names a node group of the source cluster.
- `TABLESPACE <name>` names a tablespace; on storage-decoupled clusters these are OBS-backed (`cu_obs_tbs`, `default_obs_tbs`).

Keep both when describing an existing table. Remove both before replaying the DDL against another cluster, or creation fails.

## Materialized views

Supported only on cluster version 8.2.1.220 or later **and** with `enable_matview` set to `on`. A new cluster ships with it off. Confirm both before generating materialized-view SQL; if either is unmet, use a regular view or a scheduled table instead.

## Query practices

- Use PostgreSQL-style `LIMIT`/`OFFSET`, casts, joins and window functions, within what PostgreSQL 9.2 provides.
- Filter on partition and distribution key columns where possible so DWS can prune partitions and target nodes.
- Project only needed columns; on column storage this directly reduces I/O.
- Avoid pulling large result sets through the coordinator — aggregate on the data nodes and return summaries.
- Prefer set-based statements over row-by-row DML; distribution key columns cannot be updated.
