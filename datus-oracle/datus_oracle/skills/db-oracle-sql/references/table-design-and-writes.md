# Oracle DDL and writes

## Types and DDL

- Prefer Oracle types such as `VARCHAR2(n)`, `NUMBER(p,s)`, `DATE`, `TIMESTAMP`, `CLOB`, and `BLOB`.
- Oracle 19c has no SQL `BOOLEAN` column type or `TRUE`/`FALSE` SQL literals. Store booleans as `NUMBER(1)` with `1` and `0`.
- Do not generate `DROP ... IF EXISTS`; check metadata first or use an exception-safe PL/SQL block when conditional DDL is required.
- Account for Oracle DDL's implicit commits; do not assume DDL can be rolled back with surrounding DML.

## Writes

- Do not generate multi-row `INSERT ... VALUES (...), (...)`. Use bound batch execution, separate inserts, or Oracle `INSERT ALL ... SELECT 1 FROM DUAL`.
- Do not generate PostgreSQL `ON CONFLICT` or MySQL `ON DUPLICATE KEY UPDATE`; use Oracle `MERGE` for upserts.
