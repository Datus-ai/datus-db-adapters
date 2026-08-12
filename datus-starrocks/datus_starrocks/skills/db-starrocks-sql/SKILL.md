---
name: db-starrocks-sql
description: Generate, review, and understand StarRocks SQL. Use for StarRocks queries, OLAP table DDL, DML, materialized views, catalogs, Broker Load, Stream Load, Routine Load, and rewrites where MySQL compatibility, table types, distribution, functions, or loading semantics can affect correctness.
---

# StarRocks SQL

Generate StarRocks-compatible SQL from metadata-provided object and column names. Treat the MySQL protocol as connectivity, not proof that every MySQL feature or semantic is supported.

## Namespaces and identifiers

- Address objects as `[catalog.]database.table`; use `default_catalog` for StarRocks-managed tables unless metadata selects an external catalog.
- Use `SET CATALOG catalog` to change catalog and `USE [catalog.]database` to change database context.
- Quote identifiers with backticks when needed. Use string literals rather than identifier quotes for values.
- Preserve external catalog context; do not silently collapse a three-part identifier into MySQL schema/table syntax.

## Queries, functions, and types

- Use StarRocks-supported MySQL-style query syntax, `LIMIT`, joins, common table expressions, and window functions; verify functions rather than assuming full MySQL compatibility.
- Use StarRocks types such as `BOOLEAN`, integer types including `LARGEINT`, `DECIMAL`, `CHAR`, `VARCHAR`, `STRING`, `DATE`, `DATETIME`, `JSON`, `ARRAY`, `MAP`, and `STRUCT` according to target-version and table-type support.
- Use StarRocks `DATE_TRUNC(unit, datetime)`. Do not copy a Doris argument order when generating version-sensitive date expressions.
- Use `BITMAP`, `HLL`, and percentile types only with their matching functions and table-type rules.

## OLAP table design

- Choose exactly one StarRocks table type: Duplicate Key for detail rows, Primary Key for real-time upserts and deletes, Aggregate Key for pre-aggregation, or Unique Key for legacy merge-on-read replacement semantics.
- Prefer Primary Key over Unique Key for new real-time update workloads when the target cluster supports it.
- Place key columns before value columns where required. Include partition and hash-bucketing columns in Primary, Aggregate, or Unique keys when required by that table type.
- Define partitioning for pruning and lifecycle management. Define `DISTRIBUTED BY HASH(...)` for Primary Key tables; use supported hash or random bucketing and automatic bucket counts for other table types according to target version.
- Distinguish key columns from sort keys. Use `ORDER BY` for a separately supported sort key and account for version-specific behavior when both `ORDER BY` and a key clause are present.
- Use StarRocks `PROPERTIES (...)` only for documented table properties; do not copy Doris property names or version defaults without verification.

## Writes and materialized views

- Interpret writes through the table type: Duplicate Key appends rows, Primary Key upserts the latest row, Aggregate Key merges declared aggregate values, and Unique Key replaces rows by key.
- Use partial updates and conditional updates only with a supported Primary Key configuration and the required load or DML options.
- Distinguish synchronous rollup materialized views from asynchronous materialized views. Use the correct refresh, partition, distribution, and query restrictions for the intended kind.

## Data loading capabilities

- Use `LOAD LABEL database.label (...) WITH BROKER ... PROPERTIES (...)` for asynchronous Broker Load from HDFS or cloud storage. For brokerless access in supported versions, retain the documented `WITH BROKER` keyword and provide storage credentials in properties.
- Use Stream Load through the HTTP API for synchronous request-oriented ingestion; do not represent HTTP headers as SQL clauses.
- Use Routine Load for a long-running Kafka ingestion job and manage it with the Routine Load SQL commands.
- Use `information_schema.loads` for Broker Load and INSERT job status on StarRocks 3.1+, or `SHOW LOAD [FROM database]` where appropriate. Use `SHOW ROUTINE LOAD` for Routine Load jobs.
- Use `CANCEL LOAD ... WHERE LABEL = ...` to cancel an eligible asynchronous load job.
- Treat successful Broker Load submission as job acceptance, not proof that rows are committed. Expose label, status, progress, error, and cancellation capabilities without imposing a polling workflow.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, Doris `SWITCH`, Doris-only table or load properties, PostgreSQL casts used without validation, and load syntax copied between StarRocks and Doris without checking the backend version.
