# Transwarp Inceptor SQL

Apply this reference when the current datasource uses `server_type: inceptor`.
The verified baseline is TDH CE 2024.5 / Inceptor 8.37.3 with UDF v2 and UTC.
Check a different version or session mode before extending these observations.
The connection uses HiveServer2; SQL behavior is Inceptor's, with some Oracle
compatibility features, rather than complete Apache Hive or Oracle behavior.

## Metadata and names

Prefer `list_tables`, `describe_table` and `get_table_ddl` for discovery. When
SQL-level metadata filtering is needed, this baseline exposes:

```sql
SELECT table_name, commentstring
FROM system.tables_v
WHERE database_name = 'example_db'
ORDER BY table_name
LIMIT 20;

SELECT column_name, column_type, commentstring
FROM system.columns_v
WHERE database_name = 'example_db' AND table_name = 'example_table'
ORDER BY column_id;
```

Use the actual database and table in place of the examples. Do not guess
`information_schema.table_params`: it is absent from the verified environment.
Discover metadata before substituting a dictionary from another database.
Use `database.table` qualification; do not prepend an extra schema level.

## VARCHAR2 and NULL

`DESCRIBE` may report `varchar2(100,ORACLE)`. `ORACLE` is a compatibility marker
in the type description, not a second column or evidence that the whole server
supports Oracle SQL. DDL can declare this type as `VARCHAR2(100)`; preserve the
reported type when describing existing fields.

In the verified Oracle-compatible string configuration, an empty string literal
is NULL. Consequently `value <> ''` is not a reliable nonempty-value test: it can
evaluate to UNKNOWN even for populated rows. For an optional VARCHAR2 settlement
date, use:

```sql
SELECT SUM(CASE WHEN settlement_date IS NOT NULL THEN 1 ELSE 0 END) AS settled
FROM example_db.example_loans;
```

Use the table's actual field name. If the business definition also excludes
whitespace-only values, validate an appropriate trim/length expression on the
target configuration. Do not silently apply the Oracle empty-string rule to an
Apache Hive datasource after switching targets.

## Query construction

`DUAL` is not present in the verified database. Use `SELECT 1` for literal probes.
For counts from two tables, aggregate each table in a derived query and combine:

```sql
SELECT a.loan_count, b.detail_count
FROM (SELECT COUNT(*) AS loan_count FROM example_db.example_loans) a
CROSS JOIN (SELECT COUNT(*) AS detail_count FROM example_db.example_details) b;
```

Run `SHOW TABLES IN example_db LIKE 'prefix*'` as a standalone command. To apply
SQL predicates, query `system.tables_v` rather than wrapping SHOW in a subquery.

For numeric values stored in VARCHAR2, inspect their contents and NULL handling
before casting. Keep monetary calculations in suitable DECIMAL types and use
the field's documented units rather than guessing rate or amount scaling.

Compilation failures such as an absent table or an undefined column require a
query correction or metadata lookup. Preserve the server error and verify the
corrected query; a successful metadata call does not establish that a generated
business query is correct.
