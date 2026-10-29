---
name: db-hive-sql
description: Generate and interpret HiveServer2 SQL for Apache Hive and Transwarp Inceptor, using the configured server type to select product-specific semantics.
---

# HiveServer2 SQL

Use the current datasource's runtime context and metadata to choose SQL. The
adapter type `hive` identifies the connection family; `server type` identifies
the actual product. Rules for one product do not automatically apply to another.

## Product reference

When the current datasource has **server type: inceptor**, read
[Transwarp Inceptor SQL](references/inceptor.md) **before generating SQL**.
Read it through the filesystem tool relative to this skill's `skill_location`.
Apply its rules only to the Inceptor datasource, including after switching
between two datasources that both use the `hive` adapter.

An omitted `server_type` in adapter configuration defaults to Apache Hive.
If the runtime does not identify the product and the user identifies it as
Inceptor, use that explicit target and read the reference. Do not infer the
product from a datasource name or assume Oracle compatibility from a column
type alone. Reference revision: 2026-10-29.

## Object discovery and queries

- Discover objects with `list_tables` and fields with `describe_table`. Preserve
  metadata-provided database qualification and identifier spelling.
- SQL object names use `database.table`; quote identifiers with backticks when
  needed. A Hive database is not an extra schema level to append again.
- `SHOW DATABASES`, `SHOW TABLES`, `SHOW VIEWS`, `DESCRIBE` and `SHOW CREATE TABLE`
  are standalone statements. Do not embed a `SHOW` statement in a `FROM` clause.
  For SQL filtering of metadata, use a dictionary verified for the target server.
- Use `LIMIT` to bound exploratory queries. A projected name must be a field
  from an input relation, an expression, or an alias actually defined by a
  subquery. `LIMIT 0` does not make an undefined column valid.
- Scalar expressions such as `SELECT 1` do not require an invented table.
  When combining table aggregates, join derived aggregate queries or issue
  separate queries instead of assuming Oracle's `DUAL` exists.
- Check the table's storage and transaction support before proposing writes.
  HiveServer2 connectivity does not establish support for a storage format,
  transaction mode, or every function available in another SQL engine.
