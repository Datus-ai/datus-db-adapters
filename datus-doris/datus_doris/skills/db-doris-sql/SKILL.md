---
name: db-doris-sql
description: Generate, review, and understand Apache Doris SQL. Use for Doris queries, OLAP table DDL, DML, materialized views, catalogs, Broker Load, Stream Load, Routine Load, and rewrites where MySQL compatibility, table models, distribution, functions, or loading semantics can affect correctness.
---

# Apache Doris SQL

Generate Apache Doris-compatible SQL from metadata-provided object and column names. Treat the MySQL protocol as connectivity, not proof that every MySQL feature or semantic is supported.

## Namespaces and identifiers

- Address objects as `[catalog.]database.table`; use the `internal` catalog for Doris-managed tables unless metadata selects an external catalog.
- Use `SWITCH catalog` to change catalog and `USE [catalog.]database` to change database context.
- Quote identifiers with backticks when needed. Use string literals rather than identifier quotes for values.
- Preserve catalog context for external tables; do not silently rewrite a three-part name as a MySQL schema/table name.

## Queries, functions, and types

- Use Doris-supported MySQL-style query syntax, `LIMIT`, joins, common table expressions, and window functions; verify functions rather than assuming full MySQL compatibility.
- Use Doris data types such as `BOOLEAN`, integer types including `LARGEINT`, `DECIMAL`, `CHAR`, `VARCHAR`, `STRING`, `DATE`, `DATETIME`, `TIMESTAMPTZ`, `ARRAY`, `MAP`, `STRUCT`, `JSON`, and `VARIANT` only where supported by the target version and table model.
- Use Doris `DATE_TRUNC(datetime, unit)` or the documented alternate order. Do not assume the StarRocks-only `DATE_TRUNC(unit, datetime)` convention is portable to older Doris versions.
- Use aggregate-state types such as `BITMAP`, `HLL`, `QUANTILE_STATE`, and `AGG_STATE` only with their matching functions and table-model rules.

## OLAP table design

- Choose exactly one Doris key model: `DUPLICATE KEY` to retain detail rows, `UNIQUE KEY` for latest-row/upsert semantics, or `AGGREGATE KEY` to pre-aggregate value columns.
- Place key columns before value columns and preserve the selected model's update, delete, and aggregation semantics.
- Define partitioning for pruning and lifecycle management when needed. Define `DISTRIBUTED BY HASH(...)` or `DISTRIBUTED BY RANDOM` and a suitable bucket count or automatic bucketing for the target version.
- Keep partition columns within the key where the selected model requires it. Use aggregation annotations such as `SUM`, `MAX`, `MIN`, `REPLACE`, or `BITMAP_UNION` only on Aggregate Key value columns.
- Use Doris `PROPERTIES (...)` only for documented table properties. Do not copy StarRocks property names or version defaults without verification.

## Writes and materialized views

- Interpret writes through the table model: Duplicate Key appends detail rows, Unique Key performs key-based upserts, and Aggregate Key merges value columns by their declared aggregate functions.
- Use partial-column updates only with a supported model, merge-on-write configuration, and the required session or statement option.
- Distinguish synchronous single-table materialized views from asynchronous materialized views. Use the correct `CREATE MATERIALIZED VIEW` shape, refresh clause, partitioning, and distribution syntax for the intended kind.

## Data loading capabilities

- Use `LOAD LABEL database.label (...) WITH S3|HDFS|BROKER ... PROPERTIES (...)` for asynchronous batch loading from object storage or HDFS. Treat the label as a database-scoped job identity and retry-deduplication key.
- Use Stream Load through the HTTP API for synchronous request-oriented file or row-stream ingestion; do not represent its headers as SQL clauses.
- Use Routine Load for a long-running Kafka ingestion job and manage it with the Routine Load SQL commands.
- Use `SHOW LOAD [FROM database]` to inspect Broker Load and related job state and `CANCEL LOAD ... WHERE LABEL = ...` to cancel an incomplete job.
- Treat successful `LOAD LABEL` submission as job acceptance, not proof that rows are committed. Expose status, progress, error URL, and cancellation capabilities without imposing a polling workflow.
- Use table-valued functions plus `INSERT INTO ... SELECT` for synchronous SQL-based loading when the source and target support it.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, `AUTO_INCREMENT` assumptions that ignore Doris rules, StarRocks `PRIMARY KEY` table syntax, PostgreSQL casts used without validation, and load syntax copied between Doris and StarRocks without checking the backend.
