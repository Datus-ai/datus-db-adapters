# Oracle PL/SQL

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
