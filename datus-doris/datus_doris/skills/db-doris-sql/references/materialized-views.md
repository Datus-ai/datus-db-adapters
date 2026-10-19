# Doris materialized views

Doris has two distinct kinds. Pick deliberately; they have different syntax, different limits, and different inspection commands.

## Asynchronous materialized view

An independently queryable object over one or more tables, refreshed on its own schedule, eligible for transparent rewrite.

```sql
CREATE MATERIALIZED VIEW demo.mv_gmv_daily
BUILD IMMEDIATE                       -- or DEFERRED
REFRESH AUTO ON SCHEDULE EVERY 10 MINUTE   -- or COMPLETE / ON MANUAL / ON COMMIT
PARTITION BY (DATE_TRUNC(order_time, 'DAY'))
DISTRIBUTED BY HASH(category) BUCKETS 8
PROPERTIES ("replication_num" = "1")
AS
SELECT p.category, o.order_time, SUM(oi.amount) AS gmv, COUNT(*) AS cnt
FROM demo.dwd_order_items oi
JOIN demo.dwd_orders   o ON oi.order_id   = o.order_id
JOIN demo.dim_products p ON oi.product_id = p.product_id
GROUP BY p.category, o.order_time;

REFRESH MATERIALIZED VIEW demo.mv_gmv_daily AUTO;   -- or COMPLETE / PARTITIONS (p1, p2)
DROP MATERIALIZED VIEW demo.mv_gmv_daily;
```

- `BUILD` defaults to `IMMEDIATE`. `REFRESH AUTO` refreshes only changed partitions when it can; `COMPLETE` always rebuilds everything and turns a partitioned view into an effectively unpartitioned one.
- `DISTRIBUTED BY` is optional since Doris 2.1.10 and defaults to `RANDOM`; write it out anyway. Column types cannot be declared — only names and comments.
- Partitioned incremental refresh needs a partitioned Range/List base table, exactly one partition column in `PARTITION BY`, and that column present in the `SELECT` list (and in `GROUP BY` when the query groups). Roll-up is supported only through `date_trunc`. A partition column taken from the NULL-generating side of an outer join disables incremental refresh.
- Useful properties: `grace_period` (seconds of staleness still allowed for rewrite), `excluded_trigger_tables`, `refresh_partition_num`, `use_for_rewrite` (set `false` for a view meant to be queried directly rather than to rewrite), `enable_nondeterministic_function`.
- An asynchronous view accepts no manual `INSERT`/`INSERT OVERWRITE` and no schema change.

## Synchronous materialized view

A rollup index attached to one base table, updated in the same transaction as the base table write.

```sql
CREATE MATERIALIZED VIEW sync_agg_mv AS
SELECT log_date, app_name, COUNT(*), SUM(cost)
FROM demo.app_log
GROUP BY log_date, app_name;

SHOW ALTER TABLE MATERIALIZED VIEW FROM demo;          -- creation is async: poll until FINISHED
SHOW CREATE MATERIALIZED VIEW sync_agg_mv ON app_log;
DROP MATERIALIZED VIEW sync_agg_mv ON app_log;
```

- Single table only. No `JOIN`, `HAVING`, `LIMIT`, or `LATERAL VIEW`; `WHERE`, `GROUP BY`, and `ORDER BY` are allowed.
- The select list cannot contain auto-increment columns, constants, duplicate expressions, or window functions. An aggregate must be the root expression (`sum(a + 1)` yes, `sum(a) + 1` no), and no non-aggregate expression may follow an aggregate in the list.
- Column names must not collide with base-table columns or with another sync view on the same table; alias to avoid collisions.
- On a Unique Key base table a sync view can only reorder columns, not aggregate. On Unique and Aggregate base tables a `WHERE` clause may reference key columns only.
- Many sync views on one table slow down loading, because every load writes all of them.

## Checking state and transparent rewrite

```sql
-- Is the async view built, healthy, and eligible to rewrite?
SELECT Name, State, RefreshState, SyncWithBaseTables
FROM mv_infos("database" = "demo") WHERE Name = "mv_gmv_daily";

-- Why did the last refresh fail?
SELECT * FROM tasks("type" = "mv") WHERE JobName = "<JobName from mv_infos>";

-- Did the query actually hit it? Run EXPLAIN on the ORIGINAL query, unmodified.
EXPLAIN SELECT p.category, ... ;
```

- Ready for rewrite means `State = NORMAL`, `RefreshState = SUCCESS`, and `SyncWithBaseTables = 1`. `State = SCHEMA_CHANGE` means a base table changed and rewrite is disabled until the next successful refresh, though direct queries still work.
- A refresh is asynchronous. After `CREATE` or `REFRESH`, poll `mv_infos(...)` until it reports success before concluding anything about rewrite — do not measure immediately.
- The tail of `EXPLAIN` output carries the verdict: `MaterializedViewRewriteSuccessAndChose` (used), `MaterializedViewRewriteSuccessButNotChose` (rewritten but the CBO picked another plan, often because statistics are missing), and `MaterializedViewRewriteFail` with a `FailSummary` per view. No `MaterializedView` section at all means no view was in a usable state. Use `EXPLAIN MEMO PLAN` for the detailed candidate trace.
- Keep querying the base tables. Rewrite is the point of an async view; rewriting the query to name the view yourself gives up partition-level freshness checks and the CBO's cost comparison.
- To make a view more general and hit more queries, drop filters from its definition, keep its aggregation granularity finer than the query's, and keep its filter looser than the query's.
