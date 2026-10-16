---
name: db-starrocks-sql
description: Generate and interpret StarRocks SQL with catalog, OLAP table and aggregate-state semantics. Task references cover physical table design, writes, materialized views and file or stream loading.
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

## Read existing table semantics

- Distinguish the DDL-declared table type: Duplicate Key preserves detail rows, Primary Key upserts, Aggregate Key merges declared aggregate values, and Unique Key replaces rows by key. Interpret stored measures accordingly.
- Duplicate Key and ORDER BY columns are sort keys, not evidence of uniqueness. Use actual DDL constraints when reasoning about keys or join cardinality.
- Preserve declared aggregate-state types and their matching functions; a stored bitmap or HLL state is not an ordinary additive numeric value.
- Distinguish synchronous rollup indexes from independently refreshed asynchronous materialized views when interpreting existing objects.

## Task references

Use these references for the requested operation, including SQL-generation-only requests:

- [Table design and writes](references/table-design-and-writes.md): creating or altering physical tables, writing data, and creating or refreshing materialized views.
- [Data loading](references/data-loading.md): importing files or streams, selecting loading methods, authentication, load status and failure diagnosis.

For queries and semantic modeling, use the query rules and existing-table semantics above. CREATE or INSERT in a supplied ETL is calculation evidence; it does not turn the request into a table-creation or import task.

## Avoid common dialect leaks

Treat the MySQL protocol as connectivity. Check StarRocks support and the target version before using MySQL, Doris or PostgreSQL constructs; preserve metadata-provided catalog and identifier spelling.
