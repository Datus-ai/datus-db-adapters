---
name: db-tidb-sql
description: Generate and interpret TiDB SQL with its MySQL compatibility limits, collation, ignored constraints and TiFlash behavior. Read the table-design reference for key choices, write hotspots and CTAS limits.
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

## Avoid common dialect leaks

Before returning SQL, reject StarRocks/Doris table models and distribution clauses, ClickHouse `ENGINE = MergeTree`, PostgreSQL `::` casts, MySQL storage-engine assumptions beyond the accepted-but-inert `ENGINE=InnoDB`, and any use of `CHECK` or `FULLTEXT` as if it were enforced.

## Existing key semantics

- Preserve CLUSTERED and AUTO_RANDOM comments reported by SHOW CREATE TABLE when interpreting existing keys. Foreign keys are supported on TiDB 6.6+.
- AUTO_INCREMENT values are not gap-free or globally monotonic; they are not an ordering guarantee.

## Task references

- [TiDB table design](references/table-design-and-writes.md): physical key choices, write-hotspot avoidance and supported creation syntax.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
