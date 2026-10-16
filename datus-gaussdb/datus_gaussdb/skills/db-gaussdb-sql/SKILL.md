---
name: db-gaussdb-sql
description: Generate and interpret GaussDB/openGauss SQL with PostgreSQL query syntax and A/B/PG compatibility modes. Read the task reference for distributed layout, upserts and transactions.
---

# GaussDB SQL

Generate GaussDB-compatible SQL from metadata-provided object and column names. Treat PostgreSQL 9.2 as the baseline syntax; GaussDB Kernel 50x and openGauss share this dialect. Use PostgreSQL-style query syntax in every database compatibility mode. Consult the write reference for supported upsert extensions.

## Namespaces and identifiers

- Address objects in the connected database as `schema.table`; use `public` only when metadata or connection context selects it. There is no cross-database qualification.
- Fold unquoted identifiers to lowercase. Preserve mixed case, reserved words, and special characters with double quotes; do not use backticks or square brackets.
- Avoid schema names reserved by GaussDB, including `dbe_*`, `pg_*`, `blockchain`, `snapshot`, `db4ai`, and `cstore`.

## Compatibility modes

- Each database runs in one compatibility mode: `A` (Oracle, the common GaussDB default), `B` (MySQL), or `PG`. The mode changes semantics, not accepted syntax — keep generating PostgreSQL-style SQL in every mode.
- In `A` mode, the empty string and NULL are the same value: use `col IS NULL` / `col IS NOT NULL` for emptiness checks. `col = ''` never matches, and inserting `''` stores NULL.
- In `A` mode, string concatenation follows Oracle semantics: `NULL || 'x'` returns `'x'`, not NULL.
- Do not use Oracle-only functions or syntax (`nvl`, `decode`, `sysdate`, `add_months`, `(+)` outer joins) even in `A` mode; use PostgreSQL equivalents (`coalesce`, `CASE`, `current_timestamp`, interval arithmetic, ANSI joins).
- Prefer explicit casts (`CAST(x AS type)` or `x::type`) — `A` mode implicit conversions differ from vanilla PostgreSQL, and `DATE` behaves like a timestamp there.
- In `B` mode, `date1 - date2` subtracts the dates *as numbers* (`20240315 - 20240101 = 214`), not as a day count. For a portable day difference in every mode use `to_char(d1,'J')::int - to_char(d2,'J')::int`; `DATEDIFF` exists only in `B` mode.
- In `B` mode, NULLs sort *first* ascending (opposite of PostgreSQL). When NULL placement matters — especially before `LIMIT` — always spell it out: `ORDER BY col NULLS LAST` (or `NULLS FIRST`).
- In `B` mode, `extract(week ...)` uses MySQL week numbering; use `to_char(d,'IW')` for ISO week in every mode.
- In `B` mode, boolean values render as `'1'/'0'` and aggregates over an empty set may not be NULL. For text output of booleans compare explicitly (`CASE WHEN flag THEN ...`) instead of relying on the rendering.

## Queries and types

- Use PostgreSQL-style `LIMIT` and `OFFSET`, ANSI joins, window functions, and CTEs.
- Prefer `TEXT` for unbounded strings and `VARCHAR(n)` only when a length bound is meaningful.
- Use GaussDB-supported PostgreSQL types such as `SMALLINT`, `INTEGER`, `BIGINT`, `NUMERIC`, `REAL`, `DOUBLE PRECISION`, `BOOLEAN`, `TEXT`, `BYTEA`, `DATE`, `TIMESTAMP`, and `TIMESTAMPTZ` according to metadata.
- Treat PostgreSQL features newer than 9.2 as version-dependent: `to_regclass`, `FILTER` aggregate clauses, `LATERAL`, and `ON CONFLICT` are not universally available. Confirm support before generating them.

## Avoid common dialect leaks

Before returning SQL, reject MySQL backticks and table options, Oracle-only functions and `(+)` joins, Snowflake three-part names, unguarded `ON CONFLICT`, and `= ''` emptiness checks that break under `A` mode.

## Existing distributed layout

Use the metadata's deployment type and distribution keys. Prefer joins and GROUP BY on distribution keys to reduce cross-node shuffling; a distribution key alone does not establish a unique row.

## Task references

- [GaussDB distributed layout and writes](references/table-design-and-writes.md): physical distribution choices, writes, upserts and transactions.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
