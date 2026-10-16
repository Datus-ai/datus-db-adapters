# GaussDB distributed layout and writes

## Distributed deployments

- On distributed GaussDB, large tables carry `DISTRIBUTE BY HASH (col, ...)`; reference and dimension tables may use `DISTRIBUTE BY REPLICATION`. Centralized deployments (and openGauss) take no DISTRIBUTE BY clause.
- Distribution key columns cannot be UPDATEd; never generate UPDATE statements that modify a table's distribution key.
- Prefer joins and GROUP BY on distribution keys to avoid cross-node data shuffling.

## Writes and transactions

- Use `INSERT`, `UPDATE`, and `DELETE` with PostgreSQL syntax. For upserts, prefer `INSERT ... ON DUPLICATE KEY UPDATE` or `MERGE INTO`. Use `ON CONFLICT` only in a PG-compatible database after confirming support in the target version; a version check alone is insufficient. In other compatibility modes, use the supported alternatives. `ON DUPLICATE KEY UPDATE` is a GaussDB extension accepted in every compatibility mode despite queries otherwise using PostgreSQL-style syntax. See the [GaussDB INSERT reference](https://support.huaweicloud.com/intl/en-us/centralized-ref-v10-gaussdb/gaussdb-38-0365.html).
- In `A` mode, inserting an empty string stores NULL; source data containing empty strings will not round-trip.
- Do not mix DDL and DML in one multi-statement transaction; some DDL is restricted inside explicit transactions, especially on distributed deployments.
