---
name: db-bigquery-sql
description: Generate or review Google BigQuery Standard SQL, especially when project.dataset qualification, nested data, time semantics, partition pruning, or BigQuery-specific DDL affect correctness or cost.
---

# Google BigQuery SQL

Generate GoogleSQL, not PostgreSQL or MySQL syntax.

## Names and query shape

- Address objects as ``project.dataset.table`` and quote paths containing a project ID with backticks: `` `my-project.analytics.events` ``.
- Use `QUALIFY` for filters on window-function results. Do not add a subquery only to emulate dialects that lack `QUALIFY`.
- Expand arrays with `UNNEST(array_expression)`. Access `STRUCT` fields with dot notation.
- Prefer `SAFE_CAST` and `SAFE_DIVIDE` when invalid input or zero denominators should produce `NULL` instead of failing the query.

## Time semantics

- `DATE` is a calendar date, `DATETIME` is civil time without a zone, and `TIMESTAMP` is an absolute instant.
- Pass an explicit time zone when converting a `TIMESTAMP` to a local `DATE` or `DATETIME`, for example `DATE(event_ts, 'Asia/Shanghai')`.
- Use `CURRENT_DATE('Asia/Shanghai')` when the business date is zone-specific.

## Cost and partition pruning

- Select only required columns; avoid `SELECT *` on wide or nested tables.
- Filter directly on the partition column with a constant range. Avoid wrapping the partition column in a function when an equivalent range predicate is possible.
- Use `TABLESAMPLE SYSTEM` only for approximate exploration; `LIMIT` alone does not guarantee fewer bytes scanned.
- When querying `INFORMATION_SCHEMA`, qualify it with the matching dataset or `region-REGION`; job metadata queries must run in that same region.

## DDL and migration

- Use BigQuery types such as `INT64`, `FLOAT64`, `NUMERIC`, `BIGNUMERIC`, `BOOL`, `STRING`, `BYTES`, `JSON`, `ARRAY`, and `STRUCT`.
- BigQuery has no `AUTO_INCREMENT`, `SERIAL`, storage `ENGINE`, `DISTRIBUTED BY`, or `BUCKETS` clause.
- Declare primary and foreign keys as `NOT ENFORCED`.
- Put `PARTITION BY` and then `CLUSTER BY` after the column list:

```sql
CREATE TABLE `my-project.analytics.events` (
  event_id INT64 NOT NULL,
  event_ts TIMESTAMP,
  attributes JSON,
  PRIMARY KEY (event_id) NOT ENFORCED
)
PARTITION BY DATE(event_ts)
CLUSTER BY event_id;
```
