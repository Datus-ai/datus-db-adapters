---
name: db-maxcompute-sql
description: Generate and review MaxCompute/ODPS SQL with project/schema names, partition scan costs and data-type editions. Read the task reference for partition writes, positional mappings and materialized views.
---

# MaxCompute SQL

Generate MaxCompute SQL from the configured datasource context and metadata. MaxCompute is also known as ODPS; its PyODPS SDK and Hive parser fallback do not make it a general Hive or Spark SQL engine.

## Namespaces and identifiers

- MaxCompute has no catalog level. A Datus datasource is bound to one project, so do not generate cross-project access.
- In a two-level project, qualify objects as `project.table`; there is no schema namespace.
- In a schema-enabled project, qualify objects as `project.schema.table`. Use the configured or metadata-provided schema, which defaults to `default` when none is supplied.
- Under the Datus adapter contract, a two-part identifier always means `project.table`, never `schema.table`. Use all three parts when a schema-qualified identifier is needed.
- Do not emit `USE`, `USE SCHEMA`, or `SET odps.namespace.schema` to switch context. The connector supplies the project, schema, and namespace hint for each job.
- Preserve metadata-provided names and quote reserved words or special identifiers with backticks.

## Queries and scan cost

- Add predicates on relevant partition columns when querying partitioned tables. Do not silently enable `odps.sql.allow.fullscan`.
- `LIMIT` restricts returned rows only after the distributed scan; it does not reduce bytes scanned or computing cost.
- By default, a global `ORDER BY` requires a `LIMIT`. Include a business-appropriate limit rather than inventing an unbounded sort override.
- Use MaxCompute functions and syntax. Do not borrow a MySQL, PostgreSQL, Spark, or Hive construct unless MaxCompute supports it.

## Types and expressions

- Projects can use MaxCompute V1, V2, or Hive-compatible data type editions. Follow metadata and explicit project context instead of assuming an edition.
- Prefer explicit `CAST` when type coercion is material to correctness; implicit conversion rules differ between editions.
- The adapter's `hive` parser dialect is a parsing fallback, not evidence that `odps.sql.hive.compatible` is enabled.

## Avoid dialect leaks

Before returning SQL, reject invented catalog, warehouse, cluster, distribution, storage-engine, or transaction syntax. Do not assume primary keys, row-level mutation, or Hive compatibility when the table metadata and project settings do not establish them.

## Interpret existing ETL and views

- INSERT INTO appends; INSERT OVERWRITE TABLE replaces the table or static partition. Ordinary views are logical definitions; materialized views store data.
- INSERT mappings are positional. Static partition columns are omitted from SELECT; dynamic partition expressions are last and follow the declaration order. Preserve this mapping when following a stored result's lineage.

## Task references

- [MaxCompute writes and materialized views](references/table-design-and-writes.md): INSERT mappings, partition writes, row mutation and materialized-view definitions.

Read these for the requested operation, including SQL-only authoring. Supplied ETL is evidence; follow the user's task.
