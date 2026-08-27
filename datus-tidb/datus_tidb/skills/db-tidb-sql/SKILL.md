---
name: db-tidb-sql
description: Generate, review, and understand TiDB SQL. Use for TiDB queries, DDL, DML, TiFlash columnar replicas and MPP push-down, and rewrites where MySQL compatibility, unsupported constructs, silently-ignored clauses, or write-hotspot avoidance can affect correctness or performance.
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
- **`JSON_TABLE`**, **`LATERAL` derived tables**, **`GROUPING SETS`**, **`QUALIFY`**.
- **`CREATE TABLE ... AS SELECT`** — issue `CREATE TABLE` then `INSERT INTO ... SELECT`.
- **`CORR`, `COVAR_POP`, `COVAR_SAMP`** — absent entirely, as aggregates and as window functions.
- **3-argument `DATEDIFF('day', a, b)`** — TiDB's `DATEDIFF(a, b)` takes two arguments and returns days; use `TIMESTAMPDIFF(unit, a, b)` for other units.
- **Stored procedures, triggers, events, `XA`, spatial/`GEOMETRY` types**.
- **Updatable views** — a view is read-only; `UPDATE`/`DELETE` through it is rejected.
- **Materialized views** — none exist; use a view, or a TiFlash replica for analytical speed.

Available and safe to use: window functions, CTEs, recursive CTEs, `EXCEPT`, `INTERSECT`, `WITH ROLLUP`.

## Clauses accepted but silently ignored

These raise no error, which makes them more dangerous than the ones above:

- **`CHECK` constraints** — parsed, but not enforced unless `tidb_enable_check_constraint` is `ON` (default `OFF`). Rows violating the constraint insert successfully.
- **`FULLTEXT` indexes** — accepted in `CREATE TABLE`, then dropped from the stored definition; `MATCH ... AGAINST` is rejected outright.

Do not rely on either for data integrity or search.

## TiFlash: columnar replicas and MPP

TiFlash is TiDB's columnar engine — a second copy of the same data, kept consistent through Raft. Analytical queries read it automatically once a table has a replica; no query change is needed.

- Grant a replica with `ALTER TABLE t SET TIFLASH REPLICA 1`. It is asynchronous: check `information_schema.TIFLASH_REPLICA` for `AVAILABLE = 1` before expecting columnar reads.
- The optimizer chooses between row store and columnar automatically. Force with `/*+ read_from_storage(tiflash[t]) */` or `/*+ read_from_storage(tikv[t]) */` only to diagnose a plan.
- Tables *without* a replica run analytical scans on TiKV, competing with online transactions. Prefer replicated tables for heavy aggregation, and say so when a needed table has none.

**Window functions mostly do not run in MPP.** Scans, filters, `GROUP BY` aggregation and joins push down to TiFlash and run in parallel, but only these seven window functions do:

`ROW_NUMBER` · `RANK` · `DENSE_RANK` · `LEAD` · `LAG` · `FIRST_VALUE` · `LAST_VALUE`

Everything else — `SUM`/`AVG`/`MIN`/`MAX`/`COUNT` as window functions, `STDDEV_*`, `VAR_*`, `NTILE`, `PERCENT_RANK`, `CUME_DIST`, `NTH_VALUE` — falls back to single-node computation on the TiDB layer regardless of frame or argument type. Results stay correct; parallelism is lost. When a query can be expressed either way, prefer `GROUP BY` aggregation over an equivalent aggregate window function, which does push down.

## Table design

- No distribution or bucketing clause exists — do not emit `DISTRIBUTED BY`, `DUPLICATE KEY`, `AGGREGATE KEY`, or `PROPERTIES (...)`; those are StarRocks and Doris syntax.
- Prefer `BIGINT AUTO_RANDOM PRIMARY KEY` over `AUTO_INCREMENT` for high write rates: a monotonically increasing key concentrates writes on one region. `AUTO_INCREMENT` values are unique but not gap-free or globally monotonic — never treat them as an ordering.
- An integer `PRIMARY KEY` is `CLUSTERED` by default (the row is stored in the primary-key index); `SHOW CREATE TABLE` renders this as a `/*T![clustered_index] CLUSTERED */` comment alongside `/*T![auto_rand] AUTO_RANDOM(n) */`. Preserve both when round-tripping DDL.
- `FOREIGN KEY` is supported (TiDB 6.6+), unlike in StarRocks and Doris.

## Reading EXPLAIN

TiDB's `EXPLAIN` columns are `id`, `estRows`, `task`, `access object`, `operator info` — the estimate column is `estRows`, not MySQL's `rows`. The `task` column shows where each operator runs: `root` (TiDB node), `cop[tikv]` (row store), or `mpp[tiflash]` (columnar, parallel).

## Avoid common dialect leaks

Before returning SQL, reject StarRocks/Doris table models and distribution clauses, ClickHouse `ENGINE = MergeTree`, PostgreSQL `::` casts, MySQL storage-engine assumptions beyond the accepted-but-inert `ENGINE=InnoDB`, and any use of `CHECK` or `FULLTEXT` as if it were enforced.
