---
name: db-snowflake-sql
description: Generate, review, and understand Snowflake SQL. Use for Snowflake queries, DDL, DML, MERGE, semi-structured data, stages, COPY operations, materialized views, profiling, transfers, and rewrites where identifiers, namespaces, data types, functions, constraints, or loading syntax differ from other dialects.
---

# Snowflake SQL

Generate Snowflake-compatible SQL from metadata-provided object and column names. Preserve Snowflake object resolution, type, and semi-structured data semantics.

## Namespaces and identifiers

- Qualify schema objects as `database.schema.object` when the current database and schema are not guaranteed; otherwise use the shortest unambiguous name.
- Treat warehouses, roles, and databases as account-level objects rather than schema objects.
- Remember that unquoted identifiers are stored and resolved in uppercase, while double-quoted identifiers preserve exact case and characters.
- Reuse metadata names exactly. Use double quotes for case-sensitive, reserved, spaced, or special-character identifiers; do not use backticks or square brackets.

## Queries and expressions

- Use `LIMIT` or ANSI `FETCH` for row limiting and add a deterministic `ORDER BY` for pagination.
- Use `QUALIFY` to filter window-function results without an extra subquery when it makes the query clearer.
- Use Snowflake functions such as `DATEADD`, `DATEDIFF`, `DATE_TRUNC`, `IFF`, and `TRY_TO_*` with their Snowflake argument order and return semantics.
- Use `ILIKE` for case-insensitive pattern matching when intended. Do not import another dialect's date formatting or interval syntax without conversion.
- Treat session settings such as timezone, week policy, and timestamp mapping as part of date/time semantics when interpreting results.

## Types and semi-structured data

- Use Snowflake types such as `NUMBER(p,s)`, `FLOAT`, `VARCHAR`, `BINARY`, `BOOLEAN`, `DATE`, `TIME`, `TIMESTAMP_NTZ`, `TIMESTAMP_LTZ`, and `TIMESTAMP_TZ`.
- Distinguish `TIMESTAMP_NTZ` from session-timezone-aware `TIMESTAMP_LTZ` and offset-preserving `TIMESTAMP_TZ`; do not use generic `TIMESTAMP` when the distinction matters.
- Use `VARIANT`, `OBJECT`, and `ARRAY` for semi-structured data. Parse text with functions such as `PARSE_JSON` before storing it in `VARIANT`.
- Access semi-structured paths with Snowflake path syntax and cast scalar results to the required SQL type.
- Use `LATERAL FLATTEN(INPUT => expression)` to expand `VARIANT`, `OBJECT`, or `ARRAY` values into rows.

## DDL and writes

- Use `CREATE OR REPLACE` only when replacing the object and its dependent behavior is acceptable; otherwise use `IF NOT EXISTS` or inspect metadata.
- Use `CLUSTER BY` for clustering where justified. Do not generate traditional indexes for ordinary Snowflake tables.
- Treat primary-key, unique, and foreign-key constraints on standard tables primarily as metadata; do not rely on them to enforce integrity. Account for enforced `NOT NULL` and supported `CHECK` constraints, and distinguish hybrid-table enforcement.
- Account for DDL transaction boundaries; do not assume DDL participates in a surrounding DML transaction.
- Use `MERGE` for matched update/delete and unmatched insert logic. Preserve the possibility of multiple source rows matching one target row when reasoning about correctness.

## Data loading and unloading

- Use `COPY INTO table` to load from named, user, or table stages and `COPY INTO location` to unload query or table data.
- Use existing stages and file formats from metadata when available. Otherwise make the required stage, path, file format, pattern, column mapping, and copy options explicit.
- Use a `SELECT` transform inside `COPY INTO table` when files require column reordering, casting, or semi-structured extraction.
- Treat `VALIDATION_MODE`, `ON_ERROR`, `MATCH_BY_COLUMN_NAME`, `PURGE`, and `FORCE` as explicit capabilities with data-quality and idempotency consequences; do not enable them implicitly.
- Never place secrets directly in generated SQL when a storage integration, external stage, or configured credential is available.

## Avoid common dialect leaks

Before returning SQL, reject backticks, PostgreSQL/MySQL upsert syntax, Oracle `FROM DUAL`, SQL Server `TOP`, traditional index DDL, and unqualified objects whose database and schema cannot be inferred.
