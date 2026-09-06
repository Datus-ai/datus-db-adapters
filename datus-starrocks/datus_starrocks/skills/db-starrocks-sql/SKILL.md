---
name: db-starrocks-sql
description: Generate, review, and understand StarRocks SQL. Use for StarRocks queries, OLAP table DDL, DML, materialized views, catalogs, S3 imports with FILES() or Broker Load, Stream Load, Routine Load, and rewrites where MySQL compatibility, table types, distribution, functions, or loading semantics can affect correctness.
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

### Choose a loading method

- Prefer `INSERT INTO ... SELECT ... FROM FILES(...)` for ordinary one-off S3 or HDFS imports when the target version supports the file format. It also allows previewing and transforming the source with SQL before writing.
- Check the server version with `SELECT current_version()`: `FILES()` with Parquet requires 3.1.0+, and CSV (including delimited `.txt` files) requires 3.3+. These capabilities remain available in 4.x; do not assume every 3.x release supports them. Verify other formats and optional properties against the target-version documentation.
- Preserve an explicitly requested loading method. Choose Broker Load when background/asynchronous execution is needed, or when the source format or server version is unsupported by `FILES()` but supported by Broker Load. A synchronous client timeout is not a reason to blindly resubmit the same data with another method.
- Both methods read S3 from the StarRocks cluster; they do not require a Datus S3 plugin or a download through the agent. Configure credentials and network access for the cluster, not just the SQL client. Do not assume the client's AWS CLI profile or SQL `${ENV_VAR}` placeholders are automatically forwarded or expanded. Use placeholders in proposed SQL; resolve missing authentication without exposing secrets in the conversation.

### FILES() import and validation

For execution requests, use the following workflow; for plan-only requests, provide the SQL without running it.

1. Confirm the exact source path, format, destination, and storage authentication. Put `"path"`, `"format"`, and the appropriate storage properties inside `FILES(...)`; for S3, include `"aws.s3.region"` and the chosen StarRocks-supported credential settings. Do not copy Broker Load's `WITH BROKER` clause into this function.
2. Preview with `SELECT ... FROM FILES(...) LIMIT ...` to check schema, casts, nulls, and parsing before writing. `DESC FILES(...)` is an optional schema inspection on 3.3.4+, not a prerequisite for earlier supported versions.
3. For delimited text, use `"format" = "csv"` regardless of a `.txt` suffix. Match `"csv.column_separator"` and `"csv.row_delimiter"` to the actual file, including CRLF when present; account for SQL/client escaping. Set `"csv.skip_header"` to the actual header count (zero for no header). Inspect positional columns such as `$1`, `$2`, and map them explicitly to target names with aliases and casts; skipping a header does not assign its names to columns.
4. Inspect an existing target before loading; do not silently drop, truncate, or append another copy. For a new target, create an explicit schema using the table-design rules above, or use `CREATE TABLE ... AS SELECT ... FROM FILES(...)` when inferred types are appropriate. For single-BE local tests, set `"replication_num" = "1"`; otherwise follow the deployment's replication policy instead of copying this test setting.
5. Load into an existing table with `INSERT INTO database.table (target_columns...) SELECT source_expressions... FROM FILES(...)`. This is normally synchronous; report errors or unconfirmed completion, and inspect the INSERT job/transaction status before retrying a timeout. Repeating an INSERT into a Duplicate Key table can duplicate rows.
6. After the data is visible, validate target row counts, relevant distinct keys, nulls, and representative aggregates against known source expectations. Account for pre-existing rows and the target table's append/upsert/aggregation semantics. For text, check for residual carriage returns or shifted columns. Distinguish successful execution from these data checks, and do not claim an import passed when only its SQL was generated.

Consult the official [S3 loading guide](https://docs.starrocks.io/docs/integrations/streaming/pipe/s3/) for method selection and the [FILES() reference](https://docs.starrocks.io/docs/sql-reference/sql-functions/table-functions/files/) for format, version, and authentication parameters.

### Other loading methods and job status

- Use `LOAD LABEL database.label (...) WITH BROKER ... PROPERTIES (...)` for asynchronous Broker Load from HDFS or cloud storage. For brokerless access in supported versions, retain the documented `WITH BROKER` keyword and provide storage credentials in properties.
- Use Stream Load through the HTTP API for synchronous request-oriented ingestion; do not represent HTTP headers as SQL clauses.
- Use Routine Load for a long-running Kafka ingestion job and manage it with the Routine Load SQL commands.
- Use `information_schema.loads` for Broker Load and INSERT job status on StarRocks 3.1+, or `SHOW LOAD [FROM database]` where appropriate. Use `SHOW ROUTINE LOAD` for Routine Load jobs.
- Use `CANCEL LOAD ... WHERE LABEL = ...` to cancel an eligible asynchronous load job.
- Treat successful Broker Load submission as job acceptance, not proof that rows are committed. Expose label, status, progress, error, and cancellation capabilities without imposing a polling workflow.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, Doris `SWITCH`, Doris-only table or load properties, PostgreSQL casts used without validation, and load syntax copied between StarRocks and Doris without checking the backend version.
