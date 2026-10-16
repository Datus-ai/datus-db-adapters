# Snowflake DDL and writes

## DDL and writes

- Use `CREATE OR REPLACE` only when replacing the object and its dependent behavior is acceptable; otherwise use `IF NOT EXISTS` or inspect metadata.
- Use `CLUSTER BY` for clustering where justified. Do not generate traditional indexes for ordinary Snowflake tables.
- Treat primary-key, unique, and foreign-key constraints on standard tables primarily as metadata; do not rely on them to enforce integrity. Account for enforced `NOT NULL` and supported `CHECK` constraints, and distinguish hybrid-table enforcement.
- Account for DDL transaction boundaries; do not assume DDL participates in a surrounding DML transaction.
- Use `MERGE` for matched update/delete and unmatched insert logic. Preserve the possibility of multiple source rows matching one target row when reasoning about correctness.
