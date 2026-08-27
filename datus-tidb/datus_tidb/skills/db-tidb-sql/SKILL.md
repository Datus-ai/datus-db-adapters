---
name: db-tidb-sql
description: Generate, review, and understand TiDB SQL. Use for TiDB queries and DDL where MySQL compatibility, unsupported constructs, silently-ignored clauses, or write-hotspot avoidance can affect correctness or performance.
---

# TiDB SQL

Generate TiDB-compatible SQL from metadata-provided object and column names. TiDB speaks the MySQL wire protocol and is highly MySQL-compatible, but it is a *superset-and-subset* mix, not a strict superset: treat the protocol as connectivity, never as proof that a MySQL feature exists.

## Namespaces and identifiers

- Address objects as `database.table`; TiDB has no catalog level and no separate schema level (database = schema, as in MySQL).
- Quote identifiers with backticks. Use `USE database` to change context.
- String comparison is case-sensitive by default: TiDB's default collation is `utf8mb4_bin`, not MySQL 8's `utf8mb4_0900_ai_ci`. Do not assume `WHERE name = 'alice'` matches `'Alice'`.

## Constructs TiDB does not support

Reject or rewrite these rather than emitting them:

- **`FULL OUTER JOIN`** — express as a `UNION` of keys plus `LEFT JOIN`s.
- **`JSON_TABLE`** and **`LATERAL` derived tables**.
- **`CREATE TABLE ... AS SELECT`** — issue `CREATE TABLE` then `INSERT INTO ... SELECT`.
- **`CORR`, `COVAR_POP`, `COVAR_SAMP`** — absent entirely, as aggregates and as window functions.
- **3-argument `DATEDIFF('day', a, b)`** — TiDB's `DATEDIFF(a, b)` takes two arguments and returns days; use `TIMESTAMPDIFF(unit, a, b)` for other units.
- **Materialized views** — none exist; use a view, or a TiFlash replica for analytical speed.

Available and safe to use: window functions, CTEs, recursive CTEs, `EXCEPT`, `INTERSECT`, `WITH ROLLUP`.

## Clauses accepted but silently ignored

These raise no error, which makes them more dangerous than the ones above:

- **`CHECK` constraints** — parsed, but not enforced unless `tidb_enable_check_constraint` is `ON` (default `OFF`). Rows violating the constraint insert successfully.
- **`FULLTEXT` indexes** — accepted in `CREATE TABLE`, then dropped from the stored definition; `MATCH ... AGAINST` is rejected outright.

Do not rely on either for data integrity or search.

## TiFlash: columnar replicas

TiFlash is TiDB's columnar engine — a second copy of the same data, transparent to SQL. Once a table has a replica (`information_schema.TIFLASH_REPLICA` lists them) the optimizer reads it automatically.

- Do not add `/*+ read_from_storage(tiflash[t]) */` routinely — the optimizer already chooses, and on a table with no replica the hint is ignored with a warning. Reserve it for diagnosing a plan.
- Aggregate window functions (`SUM`/`AVG`/`COUNT` over a window, `STDDEV_*`, `VAR_*`) do not run in parallel on TiFlash; where a query can be written either way, prefer `GROUP BY` aggregation.

## Table design

- No distribution or bucketing clause exists — do not emit `DISTRIBUTED BY`, `DUPLICATE KEY`, `AGGREGATE KEY`, or `PROPERTIES (...)`; those are StarRocks and Doris syntax.
- Prefer `BIGINT AUTO_RANDOM PRIMARY KEY` over `AUTO_INCREMENT` for high write rates: a monotonically increasing key concentrates writes on one region. `AUTO_INCREMENT` values are unique but not gap-free or globally monotonic — never treat them as an ordering.
- An integer `PRIMARY KEY` is `CLUSTERED` by default (the row is stored in the primary-key index); `SHOW CREATE TABLE` renders this as a `/*T![clustered_index] CLUSTERED */` comment alongside `/*T![auto_rand] AUTO_RANDOM(n) */`. Preserve both when round-tripping DDL.
- `FOREIGN KEY` is supported (TiDB 6.6+), unlike in StarRocks and Doris.

## Avoid common dialect leaks

Before returning SQL, reject StarRocks/Doris table models and distribution clauses, ClickHouse `ENGINE = MergeTree`, PostgreSQL `::` casts, MySQL storage-engine assumptions beyond the accepted-but-inert `ENGINE=InnoDB`, and any use of `CHECK` or `FULLTEXT` as if it were enforced.
