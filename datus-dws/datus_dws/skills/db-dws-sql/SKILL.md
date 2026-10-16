---
name: db-dws-sql
description: Generate and interpret GaussDB(DWS) SQL with PostgreSQL 9.2 and ORA/TD/MySQL compatibility semantics. Read the task reference for layout, DDL portability and materialized views.
---

# DWS SQL

Generate DWS-compatible SQL from metadata-provided object and column names. The baseline syntax is PostgreSQL 9.2 — DWS reports `server_version` as `9.2.4` — extended with DWS's own storage and distribution clauses. Do not assume PostgreSQL 9.3+ features are available; confirm against metadata or the target version first.

## Compatibility-mode semantics

Every database has a compatibility mode, readable from `pg_database.datcompatibility`: `ORA`, `TD` or `MySQL`. The mode changes expression semantics, not just formatting. Use the mode supplied by datasource metadata. If it is absent and database access is available, query it before generating mode-sensitive SQL:

```sql
SELECT datcompatibility
FROM pg_database
WHERE datname = current_database();
```

The mode is scoped to the database. Never infer it from `server_version`, the connection driver, or table DDL, and apply only the matching subsection below. If the mode cannot be established, avoid mode-dependent implicit conversions and state the assumption.

### ORA mode

The rules in this subsection are verified against ORA mode, which is the default for new clusters.

**These two silently produce wrong results — never ignore them:**

- **`/` is not integer division.** `7/2` yields `3.5` as `double precision`, where standard PostgreSQL yields integer `3`. When integer semantics are intended, write `floor(a/b)::int` or `div(a, b)` explicitly rather than relying on operand types.
- **The empty string is NULL.** `'' IS NULL` is true, and a stored `''` reads back as NULL. This happens at write time, so no query-side rewrite recovers it. Never use `col = ''` to test for an empty value — it matches nothing; use `col IS NULL`. Treat "empty string" and NULL as the same value when reasoning about the data.

Other verified ORA-mode differences from standard PostgreSQL:

- `'a' || NULL` yields `'a'`; concatenation absorbs NULL instead of propagating it. When PostgreSQL NULL-propagating semantics are required, test the operands explicitly.
- `DATE` is `timestamp(0) without time zone` and carries a time component; a `DATE` column reports as `timestamp without time zone` in metadata. Use `to_char(d, 'YYYY-MM-DD')` when a pure date string is wanted.
- `DATE - DATE` yields an `interval`, not an integer count of days. Use `EXTRACT(day FROM a - b)` for a numeric difference.
- `char(n)` pads on storage: `length()` returns `n`, while `=` ignores trailing blanks. Prefer `varchar(n)` unless fixed width is required.

Behaviour matching standard PostgreSQL and safe to rely on: trailing-blank sensitivity for `varchar` equality, `SUM` over an empty set returning NULL, and ISO week numbering.

### TD mode

TD mode follows Teradata-oriented coercion rules. These differ materially from ORA mode and must not inherit its empty-string, date, or division assumptions:

- **Empty strings are values.** `'' IS NULL` is false, so use `col = ''` for an empty string and `col IS NULL` for NULL. Do not merge the two cases in predicates or data-quality checks.
- **An empty string coerces to numeric zero.** For example, `''::int` yields `0`. Avoid relying on this implicit coercion: validate the input and cast explicitly so malformed or missing data is not silently treated as a real zero.
- **`DATE` stores a calendar date.** It carries year, month and day rather than ORA mode's timestamp value. Cast to `timestamp` explicitly when time-of-day semantics are required.
- **Mixed arithmetic is numeric.** DWS resolves `varchar + int` as `numeric + numeric` in TD mode. Cast the string operand explicitly and choose the desired numeric type instead of depending on implicit precision or error behaviour.
- **Mixed conditional expressions coerce types.** `CASE` and `COALESCE` can accept numeric and string branches and resolve them as text. Give their branches an explicit common type when downstream comparison, ordering or aggregation depends on the result type.
- **Do not carry ORA division behaviour into TD mode.** When integer versus fractional division matters, express the intended result with explicit numeric casts or `div(a, b)` rather than relying on operand types.

Some TD behaviours depend on session or cluster GUCs. Inspect them when the query depends on truncation, empty-string conversion functions, date formatting, NULL concatenation, or fixed-width spaces:

```sql
SHOW td_compatible_truncation;
SHOW behavior_compat_options;
```

- With `td_compatible_truncation=on`, overlong values inserted into `char` and `varchar` columns are silently truncated. Never enable it just to make generated DML succeed; validate lengths before writing.
- When `behavior_compat_options` contains `convert_empty_str_to_null_td`, `to_date('')`, `to_timestamp('')`, and `to_number('')` return NULL, and `to_char(date_value)` follows TD date formatting. Do not generalize the `''::int` result to these conversion functions; inspect this option before relying on their empty-string or date-format behaviour.
- When `behavior_compat_options` contains `strict_text_concat_td`, concatenating any value with NULL using `||` returns NULL. Otherwise do not assume that NULL behaviour; use an explicit `CASE` or `COALESCE` matching the requested result.
- When `behavior_compat_options` contains `bpchar_text_without_rtrim`, concatenating `char(n)` values preserves their padding. Use `rtrim()` explicitly when trailing spaces must not affect the result.

These TD rules are documented by DWS but are not yet covered by this adapter's live integration suite. Preserve that distinction when reporting confidence about generated SQL.

### MySQL mode

MySQL mode is **not verified**. When metadata reports it, do not apply ORA or TD semantics; state the limitation and use explicit types and NULL handling.

## Namespaces and identifiers

- Address objects as `schema.table`; use `database.schema.table` only when the metadata supplies a database. DWS has no catalog layer, so a four-part name is invalid.
- Fold unquoted identifiers to lowercase. Preserve mixed case, reserved words, and special characters with double quotes; do not use backticks or square brackets.
- Do not generate objects in `cstore`, `sys`, `pg_recyclebin`, `gs_logical_cluster`, `scheduler`, or any `dbms_*`, `utl_*`, `dbe_*` schema — these belong to DWS.

## Query practices

- Use PostgreSQL-style `LIMIT`/`OFFSET`, casts, joins and window functions, within what PostgreSQL 9.2 provides.
- Filter on partition and distribution key columns where possible so DWS can prune partitions and target nodes.
- Project only needed columns; on column storage this directly reduces I/O.
- Avoid pulling large result sets through the coordinator — aggregate on the data nodes and return summaries.

## Read existing table layout

- Preserve the full pg_get_tabledef() output when describing an existing table, including cluster-specific TABLESPACE and TO GROUP clauses. Porting that DDL is a separate operation covered by the reference.
- Distribution determines join movement, while partition predicates can prune scans. Read the actual layout; neither distribution nor sort columns establish uniqueness.

## Task references

- [DWS table design and writes](references/table-design-and-writes.md): physical layout, DDL portability, writes and materialized-view creation.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
