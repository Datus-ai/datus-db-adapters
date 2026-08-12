---
name: db-hologres-sql
description: Generate, review, and understand Alibaba Cloud Hologres SQL. Use for Hologres queries, DDL, DML, table design, foreign tables, data movement, and SQL rewrites where PostgreSQL compatibility, namespaces, storage properties, constraints, transactions, or version-specific syntax matter.
---

# Hologres SQL

Generate Hologres-compatible SQL from metadata-provided object and column names. Treat PostgreSQL 11 as the baseline syntax, but use only the subset and extensions that Hologres supports.

## Namespaces and identifiers

- Address ordinary objects in the connected database as `schema.table`; use `public` only when metadata or connection context selects it.
- Treat the connection database as context, not as an automatic SQL qualifier. Use `external_database.schema.table` only when metadata identifies a Hologres V3.0+ external database.
- Use configured foreign tables for other Hologres databases or external systems instead of assuming generic PostgreSQL cross-database qualification.
- Fold unquoted identifiers to lowercase. Preserve mixed case, reserved words, and special characters with double quotes; do not use backticks or square brackets.
- Avoid names reserved by Hologres, including column names beginning with `hg_` and schema names beginning with `holo_`, `hg_`, or `pg_`.

## Queries and types

- Use PostgreSQL-style `LIMIT` and `OFFSET`, casts, joins, window functions, and expressions only where supported by the target Hologres version.
- Prefer `TEXT` for an unbounded string and `VARCHAR(n)` only when a length bound is meaningful.
- Use Hologres-supported PostgreSQL types such as `SMALLINT`, `INTEGER`, `BIGINT`, `NUMERIC`, `REAL`, `DOUBLE PRECISION`, `BOOLEAN`, `TEXT`, `BYTEA`, `DATE`, `TIMESTAMP`, and `TIMESTAMPTZ` according to metadata.
- Confirm version support before generating newer Hologres types, functions, logical partitions, dynamic tables, or external databases.

## Internal table design

- Choose `orientation` from `column`, `row`, or `row,column` according to the workload. Require a primary key for row-oriented and row-column tables; allow it to be optional for column-oriented tables.
- Make every primary-key column `NOT NULL`. If a primary-key table has a `distribution_key`, make it the primary key or a subset of the primary-key columns.
- Make `clustering_key` and `event_time_column` columns `NOT NULL` for Hologres V1.3.28+ unless an explicitly approved compatibility setting permits nullable keys.
- Choose `distribution_key` for common joins, grouping, and shard pruning; choose `clustering_key` for point or range filters; choose `event_time_column` for time-range pruning.
- Treat `orientation`, `distribution_key`, `clustering_key`, and `event_time_column` as creation-time layout decisions that normally require table recreation to change.
- Use `WITH (property = 'value', ...)` only for Hologres V2.1+. For a version-neutral statement, create the table and call `set_table_property` in the same DDL transaction.
- Do not generate `UNIQUE`, `CHECK`, or foreign-key constraints for internal tables. Treat other PostgreSQL clauses such as generated columns and defaults as version-dependent rather than universally available.
- Use only supported list-partition syntax and verify partition-column restrictions before generating partitioned DDL.

## Writes, transactions, and external data

- Use `INSERT`, `UPDATE`, `DELETE`, and `INSERT ... ON CONFLICT` according to the table's primary-key and target-version capabilities.
- Do not assume PostgreSQL-style multi-statement DML transactions. Use `BEGIN` and `COMMIT` for supported DDL sequences such as `CREATE TABLE` plus `set_table_property`.
- Treat foreign tables as mappings to MaxCompute, OSS/DLF, or another Hologres source. Do not attach internal-table storage properties to a foreign table.
- Determine foreign-table read/write support from its server, source, and Hologres version. Use `INSERT INTO internal_table SELECT ... FROM foreign_table` when data must be stored locally.
- Treat `INSERT OVERWRITE`, `COPY`, and newer data-lake write capabilities as version-specific; do not infer support from PostgreSQL alone.

## Avoid common dialect leaks

Before returning SQL, reject MySQL backticks, MySQL table options, Snowflake three-part names, unsupported PostgreSQL constraints, unconditional cross-database qualification, and Hologres properties placed outside their supported creation syntax.
