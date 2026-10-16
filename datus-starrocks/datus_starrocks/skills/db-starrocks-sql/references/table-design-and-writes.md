# StarRocks table design and writes

## OLAP table design

- Choose exactly one StarRocks table type: Duplicate Key for detail rows, Primary Key for real-time upserts and deletes, Aggregate Key for pre-aggregation, or Unique Key for legacy merge-on-read replacement semantics.
- Prefer Primary Key over Unique Key for new real-time update workloads when the target cluster supports it.
- Place key columns before value columns where required. Include partition and hash-bucketing columns in Primary, Aggregate, or Unique keys when required by that table type.
- Define partitioning for pruning and lifecycle management. Define `DISTRIBUTED BY HASH(...)` for Primary Key tables; use supported hash or random bucketing and automatic bucket counts for other table types according to target version.
- Distinguish key columns from sort keys. Use `ORDER BY` for a separately supported sort key and account for version-specific behavior when both `ORDER BY` and a key clause are present.
- Use StarRocks `PROPERTIES (...)` only for documented table properties; do not copy Doris property names or version defaults without verification.

## Writes and materialized views

- Interpret writes through the table type: Duplicate Key appends rows, Primary Key upserts the latest row, Aggregate Key merges declared aggregate values, and Unique Key replaces rows by key.
- Use partial updates and conditional updates only with a supported Primary Key configuration and the required load or DML options.
- Distinguish synchronous rollup materialized views from asynchronous materialized views. Use the correct refresh, partition, distribution, and query restrictions for the intended kind.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, Doris `SWITCH`, Doris-only table or load properties, PostgreSQL casts used without validation, and load syntax copied between StarRocks and Doris without checking the backend version.
