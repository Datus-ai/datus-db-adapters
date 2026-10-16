---
name: db-oracle-sql
description: Generate and interpret Oracle Database 19c SQL and PL/SQL. Preserve identifier, bind, pagination, type and NULL semantics; read task references for DDL, writes, procedures, functions and packages.
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

- Use named bind variables such as `:id` for parameterized queries and writes.
- Use `FETCH FIRST n ROWS ONLY` instead of `LIMIT`.
- For pagination, use `OFFSET n ROWS FETCH NEXT m ROWS ONLY` with a deterministic `ORDER BY`.
- Use `FROM DUAL` when selecting expressions without a table, for example `SELECT 1 FROM DUAL`.
- Use literals such as `DATE '2026-01-02'` and `TIMESTAMP '2026-01-02 03:04:05'` where appropriate.
- Remember that Oracle treats an empty string as `NULL`; do not rely on distinguishing the two.

## Query types

- Prefer Oracle types such as `VARCHAR2(n)`, `NUMBER(p,s)`, `DATE`, `TIMESTAMP`, `CLOB`, and `BLOB`.
- Oracle 19c has no SQL `BOOLEAN` column type or `TRUE`/`FALSE` SQL literals. Store booleans as `NUMBER(1)` with `1` and `0`.

## Avoid common dialect leaks

Before returning SQL, reject or rewrite `LIMIT`, table aliases written with `AS`, SQL booleans, multi-row `VALUES`, `DROP ... IF EXISTS`, PostgreSQL/MySQL upsert syntax, and three- or four-part object names.

## Task references

- [Oracle DDL and writes](references/table-design-and-writes.md): conditional DDL, implicit commits, batch inserts and MERGE.
- [Oracle PL/SQL](references/plsql.md): interpreting or authoring functions, procedures, packages, parameters, blocks and side effects.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
