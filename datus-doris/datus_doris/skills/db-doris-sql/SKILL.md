---
name: db-doris-sql
description: Generate, review, and understand Apache Doris SQL. Use when the target engine is Apache Doris (not StarRocks or MySQL) for queries, OLAP DDL with the Duplicate/Unique/Aggregate key models, bucketing and partitioning, synchronous and asynchronous materialized views, multi-catalog addressing, and loading through Stream Load, Routine Load, or INSERT INTO SELECT over a TVF or catalog.
---

# Apache Doris SQL

Generate Apache Doris-compatible SQL from metadata-provided object and column names. Treat the MySQL protocol as connectivity, not proof that every MySQL feature or semantic is supported.

## Namespaces and identifiers

- Address objects as `[catalog.]database.table`; use the `internal` catalog for Doris-managed tables unless metadata selects an external catalog.
- Use `SWITCH <catalog>` to change catalog and `USE [<catalog>.]<database>` to change database context. `SWITCH` takes a single catalog identifier and never a dotted name.
- Quote identifiers with backticks when needed. Use string literals rather than identifier quotes for values.
- Preserve catalog context for external tables; do not silently rewrite a three-part name as a two-part schema/table name.
- Every catalog, external ones included, exposes `information_schema`, so `catalog.information_schema.tables` is a valid way to read metadata without switching session context.

## Queries, functions, and types

- Use Doris-supported MySQL-style query syntax, `LIMIT`, joins, common table expressions, and window functions; verify functions rather than assuming full MySQL compatibility.
- Numeric types are `BOOLEAN`, `TINYINT`, `SMALLINT`, `INT`, `BIGINT`, `LARGEINT` (128-bit), `FLOAT`, `DOUBLE`, and `DECIMAL(P[,S])`. `P` may go up to 38; values above 38 and up to 76 require the session variable `enable_decimal256 = true`.
- Date types are `DATE`, `DATETIME([P])`, `TIME([P])`, and `TIMESTAMPTZ([P])`, where `P` is a fractional-second precision in `[0, 6]` defaulting to `0`. There is no bare `TIMESTAMP` type; map an incoming `TIMESTAMP` to `DATETIME`.
- `TIMESTAMPTZ` (Doris 4.0+) stores UTC and converts on read using the session `time_zone`. It is valid as a key, partition, and bucket column.
- `TIME` exists only as an expression and result type. It cannot be stored as a column in an OLAP table — model a time of day as `VARCHAR` or fold it into a `DATETIME`.
- String types are `CHAR(M)` with `M` up to 255, `VARCHAR(M)` with `M` up to 65533 bytes, and `STRING` for anything longer. `TEXT` is the same type as `STRING`: Doris accepts it on input and prints `text` in `DESC` output.
- `IPV4` and `IPV6` are storable column types, but each holds a bare address and **stores an out-of-range or malformed value as `NULL` instead of failing**. A source column that may carry a netmask (PostgreSQL `inet`, and `cidr` always) or the other address family does not fit either one — use `VARCHAR(43)`, the longest such value. `VARBINARY` exists in Doris 4.0+ but **cannot be used in `CREATE TABLE`** — it is only reachable by mapping a binary column from an external catalog. Store binary payloads as `STRING`.
- Semi-structured types are `ARRAY<T>`, `MAP<K,V>`, `STRUCT<...>`, `JSON` (opaque binary document, fast point access), and `VARIANT` (schema-on-read, auto-expanded into sub-columns; use it for logs, traces, and evolving JSON).
- `DATE_TRUNC` accepts either argument order — `DATE_TRUNC(<datetime>, <unit>)` and `DATE_TRUNC(<unit>, <datetime>)` both resolve. The unit must be a string constant from `second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`. Prefer the order the surrounding codebase already uses rather than rewriting existing expressions.
- Use aggregate-state types `BITMAP`, `HLL`, `QUANTILE_STATE`, and `AGG_STATE` only with their matching functions (`bitmap_union` / `bitmap_union_count`, `hll_union_agg` / `hll_cardinality`, `quantile_union` / `quantile_percent`, and the `state` / `merge` / `union` combinators) and their table-model rules.

## OLAP table design

- Choose exactly one Doris key model: `DUPLICATE KEY` to retain detail rows, `UNIQUE KEY` for latest-row/upsert semantics, or `AGGREGATE KEY` to pre-aggregate value columns. These three are the only key models `CREATE TABLE` accepts, and the model cannot be changed after creation.
- Do not generate `PRIMARY KEY` as a table model. Express row-identity requirements with `UNIQUE KEY` instead.
- Always write the key clause out. Omitting it does not fail — Doris derives `AGGREGATE KEY` when any column declares an aggregate function, and otherwise `DUPLICATE KEY` over a short-key prefix of at most 3 columns or 36 bytes — but a derived key model is one nobody reviewed, and it cannot be changed without recreating the table.
- Place key columns first, in declaration order. Key columns cannot be `FLOAT`, `DOUBLE`, `STRING`, `JSON`, `VARIANT`, or a complex type; use `DECIMAL` in place of floating point and `VARCHAR` in place of string-like types.
- Key columns mean different things per model: in `DUPLICATE` they are sort columns only and need not be unique; in `UNIQUE` and `AGGREGATE` they are both sort columns and the row identity.
- In an `AGGREGATE KEY` table every non-key column requires an aggregation annotation: `SUM`, `MAX`, `MIN`, `REPLACE`, `REPLACE_IF_NOT_NULL`, `BITMAP_UNION`, `HLL_UNION`, `QUANTILE_UNION`, or `GENERIC` for an `AGG_STATE` column.
- Always write `DISTRIBUTED BY` out as well. It is optional and defaults to `RANDOM` with 10 buckets, which rarely suits the data; specify `DISTRIBUTED BY HASH(...)` with an explicit bucket count or `BUCKETS AUTO`.
- Bucket columns are constrained by the model: a `DUPLICATE` table may bucket on any column, but an `AGGREGATE` or `UNIQUE` table must bucket on key columns only (`Distribution column[x] is not key column` otherwise).
- Use `DISTRIBUTED BY RANDOM` only on a `DUPLICATE` table. Doris rejects it outright for `UNIQUE KEY`, and for an `AGGREGATE KEY` table containing a `REPLACE` or `REPLACE_IF_NOT_NULL` column.
- Bucket count is fixed once a partition is created. Prefer high-cardinality filter columns as the bucket key; `BUCKETS AUTO` sizes each partition from recent partition sizes, clamped by `autobucket_min_buckets` and `autobucket_max_buckets`.
- Partition columns must be key columns in every model, and must be `NOT NULL` unless the session sets `allow_partition_column_nullable = true`. A partition column cannot be an aggregated column.
- Complex types (`ARRAY`, `MAP`, `STRUCT`) cannot be key, partition, or bucket columns in any model; in an `AGGREGATE` table they accept only `REPLACE` or `REPLACE_IF_NOT_NULL`. `STRING` and `VARIANT` are likewise value-only.
- `AUTO_INCREMENT[(<start>)]` is supported on Duplicate Key and Unique Key tables only. The column must be `BIGINT`, `NOT NULL`, and carry no `DEFAULT`; a table may declare at most one. Generated values are unique and dense but not ordered by write time, and a user-supplied value is stored as-is without a uniqueness check.
- `ORDER BY (<cols>)` after the key clause is a Doris 4.1.0+ feature and applies to the `UNIQUE` model only, where it replaces the key columns as the data sort order. Verify the target version before generating it.
- Use Doris `PROPERTIES (...)` only for documented table properties, and size `replication_num` to the actual cluster.

```sql
-- Duplicate: append-only detail, partitioned by day, bucket on any column
CREATE TABLE demo.dwd_orders (
  order_id     BIGINT      NOT NULL,
  order_time   DATETIME    NOT NULL,
  user_id      BIGINT      NOT NULL,
  amount       DECIMAL(15,2),
  channel      VARCHAR(32)
) ENGINE=OLAP
DUPLICATE KEY(order_id, order_time)
PARTITION BY RANGE(order_time) (FROM ("2026-01-01") TO ("2026-04-01") INTERVAL 1 DAY)
DISTRIBUTED BY HASH(order_id) BUCKETS 16
PROPERTIES ("replication_num" = "1");

-- Unique: upsert dimension, merge-on-write is on by default, bucket on a key column
CREATE TABLE demo.dim_users (
  user_id      BIGINT      NOT NULL,
  name         VARCHAR(64),
  risk_status  VARCHAR(16),
  updated_at   DATETIME
) ENGINE=OLAP
UNIQUE KEY(user_id)
DISTRIBUTED BY HASH(user_id) BUCKETS 8
PROPERTIES ("replication_num" = "1");

-- Aggregate: every non-key column carries an aggregate function
CREATE TABLE demo.dws_gmv_daily (
  stat_date    DATE        NOT NULL,
  category     VARCHAR(64) NOT NULL,
  gmv          DECIMAL(20,2) SUM DEFAULT "0",
  order_cnt    BIGINT        SUM DEFAULT "0",
  buyers       BITMAP        BITMAP_UNION
) ENGINE=OLAP
AGGREGATE KEY(stat_date, category)
DISTRIBUTED BY HASH(stat_date, category) BUCKETS 8
PROPERTIES ("replication_num" = "1");
```

## Writes and updates

- Interpret writes through the table model: Duplicate Key appends detail rows, Unique Key performs key-based upserts, and Aggregate Key merges value columns by their declared aggregate functions.
- Unique Key tables use **merge-on-write by default**. The implementation is fixed at creation and cannot be changed by schema change; set `"enable_unique_key_merge_on_write" = "false"` at creation for the merge-on-read variant.
- Listing a subset of columns in `INSERT INTO` still writes a full row, filling the rest with NULL or the column default. Partial column update is opt-in and requires merge-on-write:
  - SQL: `SET enable_unique_key_partial_update = true;` before the `INSERT`.
  - Stream Load: `-H "partial_columns:true"`, or `-H "unique_key_update_mode:UPDATE_FIXED_COLUMNS"`.
  - All rows in one batch must update the same column set, unless using flexible column update (`UPDATE_FLEXIBLE_COLUMNS`, Doris 3.1.0+).
- `INSERT INTO ... SELECT` is synchronous and atomic: it either commits every row or none. `Query OK, N rows affected` plus a returned `{'label':..., 'status':..., 'txnId':...}` is the success signal; `status: committed` means the data will become visible shortly and needs no retry.
- Control error tolerance with `enable_insert_strict` (default `true`, fail on any non-conforming row). `insert_max_filter_ratio` applies only when strict mode is off and only to `INSERT INTO ... FROM S3/HDFS/LOCAL()`.

## Materialized views

Doris has two distinct kinds. Pick deliberately; they have different syntax, different limits, and different inspection commands.

### Asynchronous materialized view

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

### Synchronous materialized view

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

### Checking state and transparent rewrite

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

## Data loading

### Stream Load — synchronous HTTP ingestion

```shell
curl --location-trusted -u <user>:<password> \
  -H "Expect:100-continue" \
  -H "label:orders_20260820_01" \
  -H "column_separator:," \
  -H "columns:order_id,order_time,user_id,amount,channel" \
  -T orders.csv \
  -XPUT http://<fe_host>:8030/api/demo/dwd_orders/_stream_load
```

```shell
# JSON array in one file
curl --location-trusted -u <user>:<password> \
  -H "Expect:100-continue" \
  -H "format:json" -H "strip_outer_array:true" \
  -H "jsonpaths:[\"$.order_id\",\"$.user_id\",\"$.amount\"]" \
  -H "columns:order_id,user_id,amount" \
  -T orders.json \
  -XPUT http://<fe_host>:8030/api/demo/dwd_orders/_stream_load
```

- Submitted over HTTP, never as a SQL clause. The endpoint is `/api/{db}/{table}/_stream_load` on the **FE HTTP port** (8030 by default, not the 9030 query port).
- The FE answers with a 307 redirect to a BE, so `--location-trusted` is required to carry credentials through it. That flag resends the credentials to whatever host the FE names, so submit only to an FE you trust; use `https://<fe_host>:8050` where the cluster sets `enable_https`, which is off by default and listens on its own port rather than 8030.
- The FE chooses the redirect target, so a BE address the client cannot route to is fixed at the FE, not by retrying. Give the BEs a client-reachable `tag.public_endpoint` or `tag.private_endpoint` and select it with `-H "redirect-policy: public"` or `-H "redirect-policy: private"` (Doris 3.1.0+); `direct` forces `be_host`, and with no header the FE tries `public_endpoint`, then `private_endpoint`, then `be_host`. Posting straight to a BE's HTTP port (8040 by default) using the same path is only an option when that BE is itself reachable from the client.
- Synchronous: the response body is the result. `"Status": "Success"` means committed; `"Publish Timeout"` means committed but not yet visible and needs no retry; `"Label Already Exists"` means that label already ran — check `ExistingJobStatus`; `"Fail"` means nothing was written. Inspect bad rows with `curl "<ErrorURL>"`.
- Reuse one `label` per logical batch to get at-most-once semantics on retry. Labels are kept for 3 days by default (`label_keep_max_second`).
- Common headers: `format` (`csv` default, plus `json`, `csv_with_names`, `parquet`, `orc`, `arrow`), `column_separator`, `line_delimiter`, `where`, `partitions`, `max_filter_ratio` (0 by default), `timeout` (600s default), `strict_mode`, `timezone`, `merge_type` with `delete`, `read_json_by_line`, `json_root`, `skip_lines`, `enclose`, `escape`.
- In CSV, `\N` is NULL and an empty span between delimiters is the empty string.
- A Stream Load cannot be cancelled by the user; it ends on success, error, or timeout. History is not recorded unless `enable_stream_load_record=true` is set in `be.conf`, after which `SHOW STREAM LOAD FROM <db>` lists finished jobs.
- Keep a single file under about 10 GB (`streaming_load_max_mb` on the BE, 10240 MB default); split larger inputs.

### INSERT INTO SELECT — synchronous SQL ingestion

Use it for in-Doris ETL, for pulling from an external catalog, and for importing files through a TVF.

```sql
-- From another Doris table, into the aggregate model above
INSERT INTO demo.dws_gmv_daily (stat_date, category, gmv, order_cnt, buyers)
SELECT CAST(o.order_time AS DATE), p.category,
       SUM(oi.amount), COUNT(*), BITMAP_UNION(TO_BITMAP(o.user_id))
FROM demo.dwd_order_items oi
JOIN demo.dwd_orders   o ON oi.order_id   = o.order_id
JOIN demo.dim_products p ON oi.product_id = p.product_id
GROUP BY 1, 2;

-- From an external catalog, no pre-staging required
INSERT INTO demo.dim_users
SELECT user_id, name, risk_status, updated_at FROM mysql_ops.ops.users;

-- From files, via a table value function
DESC FUNCTION s3 (
    "uri" = "s3://bucket/path/orders_*.parquet",
    "s3.endpoint" = "https://s3.us-east-1.amazonaws.com",
    "s3.region" = "us-east-1",
    "s3.access_key" = "ak",
    "s3.secret_key" = "sk",
    "format" = "parquet"
);

INSERT INTO demo.dwd_orders (order_id, order_time, user_id, amount)
SELECT CAST(order_id AS BIGINT), CAST(order_time AS DATETIME), CAST(user_id AS BIGINT), amount
FROM s3 (
    "uri" = "s3://bucket/path/orders_*.parquet",
    "s3.endpoint" = "https://s3.us-east-1.amazonaws.com",
    "s3.region" = "us-east-1",
    "s3.access_key" = "ak",
    "s3.secret_key" = "sk",
    "format" = "parquet"
);
```

- TVFs available: `s3(...)` for S3-compatible object storage, `hdfs(...)`, `http(...)` (Doris 4.0.2+), `local(...)`, and the unified `file(...)` (Doris 3.1.0+). A TVF is a table and may appear in `FROM`, a CTE, or a join.
- Run `DESC FUNCTION <tvf>(...)` first to see the inferred schema. Parquet and ORC schemas come from file metadata; CSV and JSON are inferred from the first row and default to `string`, so cast explicitly or pass `csv_schema` as `'name1:type1;name2:type2'`. With multi-file matching, the first file's schema wins.
- Paths support `*`, `{1..10}`, and `{a,b,c}`. A path matching nothing returns an empty result set, and `DESC FUNCTION` then shows a single placeholder column `__dummy_col`.
- A `CREATE RESOURCE` of type `s3` or `hdfs` can be referenced as `"resource" = "<name>"` so credentials are not repeated in every statement; TVF properties override the resource's.
- Timeouts: FE `insert_load_default_timeout_second` and the session variable `insert_timeout`, both 4 hours by default. Size them as at least data volume divided by expected throughput.
- Inspect finished jobs with `SHOW LOAD FROM <db>` (`Type = INSERT`), and filtered rows with `SHOW LOAD WARNINGS ON "<url>"`. Wrap the statement in a `JOB` to run it asynchronously.

### Routine Load — continuous Kafka ingestion

```sql
CREATE ROUTINE LOAD demo.orders_stream ON dwd_orders
COLUMNS(order_id, order_time, user_id, amount)
PROPERTIES (
    "format" = "json",
    "jsonpaths" = "[\"$.order_id\",\"$.order_time\",\"$.user_id\",\"$.amount\"]",
    "desired_concurrent_number" = "3"
)
FROM KAFKA (
    "kafka_broker_list" = "kafka:9092",
    "kafka_topic" = "orders",
    "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);

SHOW ROUTINE LOAD FOR demo.orders_stream;
PAUSE  ROUTINE LOAD FOR demo.orders_stream;
RESUME ROUTINE LOAD FOR demo.orders_stream;
STOP   ROUTINE LOAD FOR demo.orders_stream;
```

- For CSV topics, use `COLUMNS TERMINATED BY ","` before the `COLUMNS(...)` clause instead of the JSON properties.
- `ALTER ROUTINE LOAD` requires the job to be paused first, then resumed. `STOP` deletes the job irreversibly and it disappears from `SHOW ROUTINE LOAD`.
- One job spawns many subtasks; `SHOW ROUTINE LOAD` reports job-level state and lag, and the task view reports per-subtask consumption progress.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, `AUTO_INCREMENT` declarations that ignore the Doris type and table-model rules, `PRIMARY KEY` used as a table model, `FOREIGN KEY`, `CHECK`, and `FULLTEXT` clauses, `TIME` or `VARBINARY` used as stored columns, a bucket column that is not a key column on a Unique or Aggregate table, a partition column that is not a key column, PostgreSQL casts used without validation, and table properties or load syntax carried over from another OLAP engine without checking them against the target Doris version.
