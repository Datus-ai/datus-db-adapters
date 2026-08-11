---
name: db-oracle-sql
description: Generate, review, and understand Oracle Database 19c SQL and PL/SQL. Use for Oracle queries, DDL, DML, stored procedures, functions, packages, anonymous blocks, profiling, transfers, and SQL rewrites where namespace, identifier, pagination, data type, alias, procedural, or write syntax differs from other dialects.
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

## PL/SQL program units

- Recognize procedures, functions, package specifications and bodies, triggers, and anonymous blocks as PL/SQL units. Procedures, functions, and anonymous blocks have an optional declarative part, a required executable part, and an optional exception-handling part.
- Treat a procedure as a callable unit without a direct return value; use `OUT` or `IN OUT` parameters for outputs. Treat a function as a callable unit with a declared `RETURN` type and `RETURN` statements.
- Treat a package specification as the public interface and its package body as the implementation plus private declarations. Resolve packaged members as `SCHEMA.PACKAGE.PROCEDURE` or `SCHEMA.PACKAGE.FUNCTION`.
- Recognize overloaded subprograms by their parameter signatures instead of assuming that a name identifies only one procedure or function.
- Treat `BEGIN ... END;` and `DECLARE ... BEGIN ... END;` as complete anonymous blocks. Keep their internal semicolons intact instead of splitting them into ordinary SQL statements.
- Omit the trailing `/` when sending PL/SQL through a driver; `/` is a SQL*Plus-style client command that submits the preceding block.

## PL/SQL parameters and results

- Interpret parameter modes as `IN` for input, `OUT` for output, and `IN OUT` for a value passed in and returned with possible changes. Recognize omitted modes as `IN`.
- Recognize default parameter values and positional, named (`formal => actual`), and mixed invocation notation when resolving arguments.
- Recognize `%TYPE` and `%ROWTYPE` declarations as types anchored to database columns, rows, variables, or cursors rather than standalone type names.
- Recognize explicit cursors, cursor `FOR` loops, and `SYS_REFCURSOR`. A REF CURSOR is a handle to a result set that a procedure can expose through an `OUT` or `IN OUT` parameter, or that a function can return directly; it is not a direct procedure return value.
- Distinguish SQL types from PL/SQL-only types: Oracle 19c table columns cannot use `BOOLEAN`, while PL/SQL variables and parameters can.
- Treat `DBMS_OUTPUT.PUT_LINE` as diagnostic output that clients must explicitly enable and fetch, not as a return value or query result.

## PL/SQL control flow and effects

- Interpret `IF`, `CASE`, basic and cursor `LOOP` forms, local subprograms, and nested blocks as procedural control flow around embedded SQL.
- Treat `SELECT ... INTO` as a single-row assignment that can raise `NO_DATA_FOUND` or `TOO_MANY_ROWS`; distinguish it from a query result returned to the caller.
- Interpret `EXCEPTION` handlers according to their control flow. `WHEN OTHERS` suppresses the original failure unless it executes `RAISE` or raises another exception.
- Treat `OPEN ref_cursor FOR dynamic_string USING ...` as a dynamic query with input binds supplied by `USING`; its rows are consumed later with `FETCH`. Distinguish it from a static `OPEN ref_cursor FOR SELECT ...`.
- Treat `EXECUTE IMMEDIATE` separately: `USING` supplies input binds, `INTO` receives single-row query outputs, and `RETURNING INTO` receives DML outputs where applicable.
- Assume a stored subprogram shares the caller's transaction unless it issues `COMMIT` or `ROLLBACK` or declares `PRAGMA AUTONOMOUS_TRANSACTION`. Do not infer that an unhandled exception automatically rolls back prior work; the caller or host controls the transaction outcome.
- Recognize `AUTHID DEFINER` as definer-rights execution and `AUTHID CURRENT_USER` as invoker-rights execution. Account for calls to other routines and triggers when reasoning about reads, writes, privileges, and side effects.

## Avoid common dialect leaks

Before returning SQL, reject or rewrite `LIMIT`, table aliases written with `AS`, SQL booleans, multi-row `VALUES`, `DROP ... IF EXISTS`, PostgreSQL/MySQL upsert syntax, and three- or four-part object names.
