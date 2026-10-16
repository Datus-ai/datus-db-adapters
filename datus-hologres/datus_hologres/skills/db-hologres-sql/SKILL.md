---
name: db-hologres-sql
description: Generate and interpret Hologres SQL with its PostgreSQL subset, namespaces, types and foreign-table semantics. Read the task reference for physical layout, DDL and writes.
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

## Avoid common dialect leaks

Before returning SQL, reject MySQL backticks, MySQL table options, Snowflake three-part names, unsupported PostgreSQL constraints, unconditional cross-database qualification, and Hologres properties placed outside their supported creation syntax.

## Read existing table semantics

- Use declared primary-key metadata; orientation, distribution_key, clustering_key and event_time_column describe storage or pruning and do not by themselves establish uniqueness.
- Foreign tables map to MaxCompute, OSS/DLF or another Hologres source. Resolve their actual server and source context when interpreting SQL rather than treating them as locally stored internal tables.

## Task references

- [Hologres table design and writes](references/table-design-and-writes.md): internal and foreign-table creation, storage properties, writes and transactions.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
