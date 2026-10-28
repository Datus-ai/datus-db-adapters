---
name: db-hana-sql
description: Discover SAP HANA tables, inspect columns, and generate HANA SQL while preserving quoted identifiers and tenant/schema boundaries.
---

# SAP HANA SQL

Apply these rules to HANA metadata tools, queries, and semantic modeling.

## Namespaces and identifiers

- The configured database selects the tenant at connection time. SQL object names use `schema.table`; do not prefix them with a tenant such as `HXE` or `SYSTEMDB`.
- Unquoted identifiers fold to uppercase. Quoted identifiers preserve their exact spelling, including lowercase, mixed case, spaces, embedded dots, and escaped double quotes. Keep metadata-provided quoting for both tables and columns.
- `list_tables` returns ready-to-use identifiers in `qualified_name`. Pass the entire value **unchanged** as `table_name` to `describe_table` or `get_table_ddl`, including every double-quote character and the schema qualification. This also applies when `schema_name` is supplied separately.
- Use that same quoted identifier in SQL. A bare table name, an alias, or a string label from a query result does not replace the metadata identifier.
- If metadata output is truncated, use `read_tool_result` to read the complete result before choosing identifiers.

For example, if `qualified_name` is `"APP"."orders"`, the metadata tool arguments are:

```json
{"table_name": "\"APP\".\"orders\"", "schema_name": "APP"}
```

Passing `orders` or `APP.orders` would request the unquoted uppercase name `ORDERS`, which is a different object. Do not remove the quotes just because the schema is already supplied as an argument.

If a metadata lookup fails, compare the submitted identifier with the exact `qualified_name` returned by discovery. Reuse the original identifier rather than trying case variants or guessing system catalog tables and columns. If discovery does not contain the requested object, report that evidence.

## Queries and metadata

- Use verified common SELECT syntax, joins, CTEs, aggregations, window functions, and `LIMIT`. A literal query uses `FROM DUMMY`, for example `SELECT 1 FROM DUMMY`.
- Use double quotes for identifiers and single quotes for string values. Preserve the exact column names returned by `describe_table`.
- Discover objects with `list_tables` and inspect their columns with `describe_table`. Use `get_table_ddl` only when it is available in the current toolset. A missing tool does not justify inventing a system-catalog query.
- Table DDL returned by this adapter describes columns. It does not establish constraints, defaults, physical layout, or lossless migration behavior. Do not infer keys or relationships from this DDL alone.

## Local SQL analysis boundary

- Execute the original SQL. The compatible parser used for local analysis does not make HANA a PostgreSQL datasource and does not justify rewriting SQL into PostgreSQL syntax.
- Local analysis supports a verified common SELECT subset. SQLScript, Calculation View parameters, table functions, sequence access, multi-statement input, SELECT INTO, and locking queries remain outside that subset.
- Keep tenant selection in datasource configuration. HXE and SYSTEMDB are separate connection targets; qualifying a SQL name with a tenant does not switch the connection.
