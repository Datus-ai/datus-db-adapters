---
name: db-bigquery-sql
description: Generate and review BigQuery GoogleSQL with project/dataset names, nested data, time zones and partition pruning. Read the DDL reference for table creation and migration.
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

## Types and existing constraints

- Use GoogleSQL type names such as INT64, NUMERIC, BOOL and STRING, including ARRAY and STRUCT.
- Primary and foreign keys are NOT ENFORCED. Their presence in DDL does not prove the existing rows satisfy them; preserve that distinction in relationship and cardinality reasoning.

## Task references

- [BigQuery DDL and migration](references/table-design-and-writes.md): physical table creation, partitioning, clustering and declared constraints.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
