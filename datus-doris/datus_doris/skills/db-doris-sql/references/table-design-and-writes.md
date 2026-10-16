# Doris table design and writes

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
