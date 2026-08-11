---
name: db-oracle-sql
description: Generate and review Oracle Database 19c-compatible SQL. Use for Oracle queries, DDL, DML, profiling, transfers, and SQL rewrites where namespace, identifier, pagination, data type, alias, or write syntax differs from PostgreSQL, MySQL, Snowflake, and other dialects.
---

# Oracle SQL

Generate Oracle Database 19c-compatible SQL. Prefer metadata-provided object and column names, and apply the following rules to every generated statement.

## Namespaces and identifiers

- Treat the service name or PDB as a connection target, not an SQL namespace.
- Qualify objects as `"SCHEMA"."TABLE"`. Do not generate catalog or database prefixes.
- Oracle folds unquoted identifiers to uppercase. Use uppercase double-quoted identifiers for schemas, tables, and columns, especially for reserved words or special characters.
- Use `AS` for column aliases when useful, but never put `AS` before a table or subquery alias.
- Do not use backticks or square brackets for identifiers.

## Queries

- Use `FETCH FIRST n ROWS ONLY` instead of `LIMIT`.
- For pagination, use `OFFSET n ROWS FETCH NEXT m ROWS ONLY` with a deterministic `ORDER BY`.
- Use `FROM DUAL` when selecting expressions without a table, for example `SELECT 1 FROM DUAL`.
- Use literals such as `DATE '2026-01-02'` and `TIMESTAMP '2026-01-02 03:04:05'` where appropriate.
- Remember that Oracle treats an empty string as `NULL`; do not rely on distinguishing the two.

## Types and DDL

- Prefer Oracle types such as `VARCHAR2(n)`, `NUMBER(p,s)`, `DATE`, `TIMESTAMP`, `CLOB`, and `BLOB`.
- Oracle 19c has no SQL `BOOLEAN` column type or `TRUE`/`FALSE` SQL literals. Store booleans as `NUMBER(1)` with `1` and `0`.
- Do not generate `DROP ... IF EXISTS`; check metadata first or use an exception-safe PL/SQL block when conditional DDL is required.
- Account for Oracle DDL's implicit commits; do not assume DDL can be rolled back with surrounding DML.

## Writes

- Use named bind variables such as `:id` for parameterized statements.
- Do not generate multi-row `INSERT ... VALUES (...), (...)`. Use bound batch execution, separate inserts, or Oracle `INSERT ALL ... SELECT 1 FROM DUAL`.
- Do not generate PostgreSQL `ON CONFLICT` or MySQL `ON DUPLICATE KEY UPDATE`; use Oracle `MERGE` for upserts.

## Avoid common dialect leaks

Before returning SQL, reject or rewrite `LIMIT`, table aliases written with `AS`, SQL booleans, multi-row `VALUES`, `DROP ... IF EXISTS`, PostgreSQL/MySQL upsert syntax, and three- or four-part object names.
