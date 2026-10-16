---
name: db-doris-sql
description: Generate and interpret Apache Doris SQL with catalog, OLAP key-model and aggregate-state semantics. Task references cover physical tables, writes, loading, and materialized-view queries or rewrite diagnosis.
---

# Apache Doris SQL

Generate Apache Doris-compatible SQL from metadata-provided object and column names. Treat the MySQL protocol as connectivity, not proof that every MySQL feature or semantic is supported.

## Namespaces and identifiers

- Address objects as `[catalog.]database.table`; use the `internal` catalog for Doris-managed tables unless metadata selects an external catalog.
- Use `SWITCH <catalog>` to change catalog and `USE [<catalog>.]<database>` to change database context. `SWITCH` takes a single catalog identifier and never a dotted name.
- Quote identifiers with backticks when needed. Use string literals rather than identifier quotes for values.
- Preserve catalog context for external tables; do not silently rewrite a three-part name as a two-part schema/table name.
- Every catalog, external ones included, exposes `information_schema`, so `catalog.information_schema.tables` is a valid way to read metadata without switching session context.

## Queries, functions, and types

- Use Doris-supported MySQL-style query syntax, `LIMIT`, joins, common table expressions, and window functions; verify functions rather than assuming full MySQL compatibility.
- Numeric types are `BOOLEAN`, `TINYINT`, `SMALLINT`, `INT`, `BIGINT`, `LARGEINT` (128-bit), `FLOAT`, `DOUBLE`, and `DECIMAL(P[,S])`. `P` may go up to 38; values above 38 and up to 76 require the session variable `enable_decimal256 = true`.
- Date types are `DATE`, `DATETIME([P])`, `TIME([P])`, and `TIMESTAMPTZ([P])`, where `P` is a fractional-second precision in `[0, 6]` defaulting to `0`. There is no bare `TIMESTAMP` type; map an incoming `TIMESTAMP` to `DATETIME`.
- `TIMESTAMPTZ` (Doris 4.0+) stores UTC and converts on read using the session `time_zone`. It is valid as a key, partition, and bucket column.
- `TIME` exists only as an expression and result type. It cannot be stored as a column in an OLAP table — model a time of day as `VARCHAR` or fold it into a `DATETIME`.
- String types are `CHAR(M)` with `M` up to 255, `VARCHAR(M)` with `M` up to 65533 bytes, and `STRING` for anything longer. `TEXT` is the same type as `STRING`: Doris accepts it on input and prints `text` in `DESC` output.
- `IPV4` and `IPV6` are storable column types, but each holds a bare address and **stores an out-of-range or malformed value as `NULL` instead of failing**. A source column that may carry a netmask (PostgreSQL `inet`, and `cidr` always) or the other address family does not fit either one — use `VARCHAR(43)`, the longest such value. `VARBINARY` exists in Doris 4.0+ but **cannot be used in `CREATE TABLE`** — it is only reachable by mapping a binary column from an external catalog. Store binary payloads as `STRING`.
- Semi-structured types are `ARRAY<T>`, `MAP<K,V>`, `STRUCT<...>`, `JSON` (opaque binary document, fast point access), and `VARIANT` (schema-on-read, auto-expanded into sub-columns; use it for logs, traces, and evolving JSON).
- `DATE_TRUNC` accepts either argument order — `DATE_TRUNC(<datetime>, <unit>)` and `DATE_TRUNC(<unit>, <datetime>)` both resolve. The unit must be a string constant from `second`, `minute`, `hour`, `day`, `week`, `month`, `quarter`, `year`. Prefer the order the surrounding codebase already uses rather than rewriting existing expressions.
- Use aggregate-state types `BITMAP`, `HLL`, `QUANTILE_STATE`, and `AGG_STATE` only with their matching functions (`bitmap_union` / `bitmap_union_count`, `hll_union_agg` / `hll_cardinality`, `quantile_union` / `quantile_percent`, and the `state` / `merge` / `union` combinators) and their table-model rules.

## Avoid common dialect leaks

Before returning SQL, reject MySQL storage engines, `AUTO_INCREMENT` declarations that ignore the Doris type and table-model rules, `PRIMARY KEY` used as a table model, `FOREIGN KEY`, `CHECK`, and `FULLTEXT` clauses, `TIME` or `VARBINARY` used as stored columns, a bucket column that is not a key column on a Unique or Aggregate table, a partition column that is not a key column, PostgreSQL casts used without validation, and table properties or load syntax carried over from another OLAP engine without checking them against the target Doris version.

## Read existing table semantics

- DUPLICATE KEY columns are sort columns and need not be unique. UNIQUE KEY and AGGREGATE KEY have different row-replacement or pre-aggregation behavior; read the table's actual key model before interpreting stored values.
- AGGREGATE KEY value columns carry their own functions such as SUM, MAX, REPLACE or state-union functions. Preserve those meanings when modeling measures; aggregate states are not ordinary additive numeric values.
- AUTO_INCREMENT values supplied by users are stored without a uniqueness check; generated identity values do not prove the complete stored column is unique.
- Synchronous materialized views are rollup indexes attached to a base table; asynchronous views are independently queryable and refreshed on their own schedule. Use the materialized-view reference for query and rewrite diagnosis.

## Task references

- [Doris table design and writes](references/table-design-and-writes.md): creating or altering physical tables and writing data.
- [Doris materialized views](references/materialized-views.md): understanding or querying existing views, diagnosing transparent rewrite and refresh state, and creating or refreshing views.
- [Doris data loading](references/data-loading.md): choosing loading methods, importing data, authentication, load status and failures.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
