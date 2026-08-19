---
name: db-doris-sql
description: Generate, review, and understand Apache Doris SQL. Use for Doris queries, OLAP table DDL, DML, materialized views, catalogs, Broker Load, Stream Load, Routine Load, and rewrites where MySQL compatibility, table models, distribution, functions, or loading semantics can affect correctness.
---

# Apache Doris SQL

Generate Apache Doris-compatible SQL from metadata-provided object and column names. Treat the MySQL protocol as connectivity, not proof that every MySQL feature or semantic is supported.

## Namespaces and identifiers

- Address objects as `[catalog.]database.table`; use the `internal` catalog for Doris-managed tables unless metadata selects an external catalog.
- Use `SWITCH catalog` to change catalog and `USE [catalog.]database` to change database context. `SWITCH` takes a single catalog identifier and never a dotted name.
- Quote identifiers with backticks when needed. Use string literals rather than identifier quotes for values.
- Preserve catalog context for external tables; do not silently rewrite a three-part name as a two-part schema/table name.
- Every catalog, external ones included, exposes `information_schema`, so `catalog.information_schema.tables` is a valid way to read metadata without switching session context.

## Queries, functions, and types

- Use Doris-supported MySQL-style query syntax, `LIMIT`, joins, common table expressions, and window functions; verify functions rather than assuming full MySQL compatibility.
- Use Doris data types such as `BOOLEAN`, integer types including `LARGEINT`, `DECIMAL`, `CHAR`, `VARCHAR`, `STRING`, `DATE`, `DATETIME`, `ARRAY`, `MAP`, `STRUCT`, `JSON`, and `VARIANT` only where supported by the target version and table model.
- Treat `TIMESTAMPTZ`, `IPV4`, `IPV6`, and `VARBINARY` as newer types: confirm target-version support before generating them, and fall back to `DATETIME` or `VARCHAR` when the version is unknown.
- Bound string types by their limits: `VARCHAR` holds at most 65533 bytes and `CHAR` at most 255. Use `STRING` for longer values. `TEXT` is accepted as an alias for `STRING`.
- Doris accepts `DATE_TRUNC` with either argument order — `DATE_TRUNC(datetime, unit)` and `DATE_TRUNC(unit, datetime)` both resolve. Prefer the order the surrounding codebase already uses rather than rewriting existing expressions.
- Use aggregate-state types such as `BITMAP`, `HLL`, `QUANTILE_STATE`, and `AGG_STATE` only with their matching functions and table-model rules.

## OLAP table design

- Choose exactly one Doris key model: `DUPLICATE KEY` to retain detail rows, `UNIQUE KEY` for latest-row/upsert semantics, or `AGGREGATE KEY` to pre-aggregate value columns. These three are the only key models `CREATE TABLE` accepts.
- Do not generate `PRIMARY KEY` as a table model. Express row-identity requirements with `UNIQUE KEY` instead.
- Always write the key clause out. Omitting it does not fail — Doris derives `AGGREGATE KEY` when any column declares an aggregate function, and otherwise `DUPLICATE KEY` over a short-key prefix of at most 3 columns or 36 bytes — but a derived key model is one nobody reviewed, and it cannot be changed without recreating the table.
- Place key columns first, in declaration order. Key columns cannot be `FLOAT`, `DOUBLE`, `STRING`, `JSON`, `VARIANT`, or a complex type; use `DECIMAL` in place of floating point and `VARCHAR` in place of string-like types.
- In an `AGGREGATE KEY` table every non-key column requires an aggregation annotation such as `SUM`, `MAX`, `MIN`, `REPLACE`, `REPLACE_IF_NOT_NULL`, `BITMAP_UNION`, or `HLL_UNION`.
- Always write `DISTRIBUTED BY` out as well. It is optional and defaults to random distribution with 10 buckets, which rarely suits the data; specify `DISTRIBUTED BY HASH(...)` with an explicit bucket count or `BUCKETS AUTO`.
- Do not combine `DISTRIBUTED BY RANDOM` with `UNIQUE KEY`; it is also rejected for an `AGGREGATE KEY` table containing `REPLACE` or `REPLACE_IF_NOT_NULL` columns.
- `AUTO_INCREMENT` is supported on Duplicate Key and Unique Key tables only. The column must be `BIGINT NOT NULL` without a default value, and a table may declare at most one.
- Doris rejects `TIME` columns on OLAP tables. Model a time-of-day value as `VARCHAR` or fold it into a `DATETIME`.
- `CREATE TABLE` accepts an optional `ORDER BY (...)` sort key alongside the key clause; treat it as version-sensitive and verify support before generating it.
- Define partitioning for pruning and lifecycle management when needed, and keep partition columns within the key where the selected model requires it.
- Use Doris `PROPERTIES (...)` only for documented table properties, and size `replication_num` to the actual cluster.

## Writes and materialized views

- Interpret writes through the table model: Duplicate Key appends detail rows, Unique Key performs key-based upserts, and Aggregate Key merges value columns by their declared aggregate functions.
- Use partial-column updates only with a supported model, merge-on-write configuration, and the required session or statement option.
- Distinguish synchronous single-table materialized views from asynchronous materialized views. A synchronous view is a rollup index attached to its base table and is inspected with `SHOW CREATE MATERIALIZED VIEW <name> ON <table>`; an asynchronous view is an independently queryable object listed by `mv_infos()` and inspected with `SHOW CREATE MATERIALIZED VIEW <name>`.
- Use the correct `CREATE MATERIALIZED VIEW` shape, refresh clause, partitioning, and distribution syntax for the intended kind. An asynchronous view cannot reference an auto-increment column.

## Data loading capabilities

- Use `LOAD LABEL database.label (...) WITH S3|HDFS|BROKER ... PROPERTIES (...)` for asynchronous batch loading from object storage or HDFS. Treat the label as a database-scoped job identity and retry-deduplication key.
- Use Stream Load through the HTTP API for synchronous request-oriented file or row-stream ingestion; do not represent its headers as SQL clauses.
- Use Routine Load for a long-running Kafka ingestion job and manage it with the Routine Load SQL commands.
- Use `SHOW LOAD [FROM database]` to inspect Broker Load and related job state and `CANCEL LOAD ... WHERE LABEL = ...` to cancel an incomplete job.
- Treat successful `LOAD LABEL` submission as job acceptance, not proof that rows are committed. Expose status, progress, error URL, and cancellation capabilities without imposing a polling workflow.
- Use table-valued functions plus `INSERT INTO ... SELECT` for synchronous SQL-based loading when the source and target support it.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, `AUTO_INCREMENT` declarations that ignore the Doris type and table-model rules, `PRIMARY KEY` used as a table model, `FOREIGN KEY`, `CHECK`, and `FULLTEXT` clauses, PostgreSQL casts used without validation, and table properties or load syntax carried over from another OLAP engine without checking them against the target Doris version.
