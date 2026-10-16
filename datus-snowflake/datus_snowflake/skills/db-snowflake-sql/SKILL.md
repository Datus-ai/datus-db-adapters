---
name: db-snowflake-sql
description: Generate and interpret Snowflake SQL with case-sensitive names, time semantics, semi-structured data and standard/hybrid-table constraints. Task references cover DDL, MERGE, stages and COPY.
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

## Avoid common dialect leaks

Before returning SQL, reject backticks, PostgreSQL/MySQL upsert syntax, Oracle `FROM DUAL`, SQL Server `TOP`, traditional index DDL, and unqualified objects whose database and schema cannot be inferred.

## Existing constraints

Treat primary-key, unique and foreign-key constraints on standard tables primarily as metadata; they do not establish enforced integrity. Distinguish the enforced constraints of hybrid tables and the target version. Keep declared keys separate from demonstrated row uniqueness.

## Task references

- [Snowflake DDL and writes](references/table-design-and-writes.md): object replacement, table design, MERGE and transaction boundaries.
- [Snowflake loading and unloading](references/data-loading.md): COPY, stages, file formats, storage authentication and load options.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
