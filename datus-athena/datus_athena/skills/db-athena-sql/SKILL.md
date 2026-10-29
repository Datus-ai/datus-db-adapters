---
name: db-athena-sql
description: Generate and review Athena SQL engine v3 queries using Trino expressions, Hive DDL, catalog/database names, nested data and partition pruning. Read the write reference for S3 tables, CTAS, INSERT and Iceberg mutations.
---

# Amazon Athena SQL

Target the configured Athena **SQL engine v3** Workgroup. Athena supports a subset of Trino DML and Hive DDL; it is not a general Trino server, Spark SQL session, PostgreSQL database or MySQL server. Check Athena support before using a function copied from newer Trino documentation.

## Names and statement families

- SQL relations use `catalog.database.table`, `database.table`, or a table in the configured database. In Datus, leave `schema_name` empty: Glue databases occupy the database level, with no fourth schema component.
- Discover names and types with the available metadata tools. Preserve each component of metadata-provided `qualified_name`; quote components individually, not the entire dotted path. Supply catalog/database in their corresponding tool arguments.
- In SELECT, INSERT, CTAS and view queries, use double quotes for identifiers and single quotes for string values. Hive-style `CREATE EXTERNAL TABLE` and `ALTER TABLE` use backticks for escaped identifiers. Do not carry query-side `VARCHAR`/`ROW` declarations blindly into Hive column DDL.
- Read actual metadata rather than applying PostgreSQL case-folding or treating quoted names as case-sensitive HANA names. Athena/Glue names are generally case-insensitive; keep the returned spelling and escaping for spaces, punctuation and reserved words.

For a discovered query relation `"AwsDataCatalog"."analytics"."orders"`, metadata arguments can be:

```json
{"table_name": "\"AwsDataCatalog\".\"analytics\".\"orders\"", "catalog": "AwsDataCatalog", "database": "analytics", "schema_name": ""}
```

## Port SQL deliberately

Use these Athena forms when translating queries from other engines:

| Portability trap | Athena query form |
| --- | --- |
| `TOP n`, MySQL `LIMIT offset, count` | End with `OFFSET offset LIMIT count`, or just `LIMIT count`; use ORDER BY for repeatable pages. |
| `QUALIFY row_number() ... = 1` | Compute the window function in a CTE/subquery, then filter its output in an outer WHERE. |
| `IFNULL(x, y)` / `NVL(x, y)` | `COALESCE(x, y)`; keep argument types compatible. |
| `SAFE_CAST(x AS BIGINT)` or PostgreSQL `x::BIGINT` | `TRY_CAST(x AS BIGINT)` for invalid input to become NULL; `CAST` when invalid input must fail. |
| SQL Server `DATEADD(day, n, t)` | `date_add('day', n, t)`; differences use `date_diff('day', start_time, end_time)`. |
| String `+`, implicit numeric/string concatenation | `concat(text, CAST(number AS VARCHAR))` or `text || CAST(number AS VARCHAR)`. |
| Spark `explode`, Snowflake `FLATTEN` | `CROSS JOIN UNNEST(items) AS u(item)` for an ARRAY; a typed ROW field uses dot notation. |
| Zero-based SQL ARRAY access | `items[1]` or `element_at(items, 1)` is the first element. JSONPath indexes remain zero-based. |
| JSON text treated as a typed ROW | `json_extract_scalar(payload, '$.customer.id')` returns scalar VARCHAR; use `json_extract` for JSON objects/arrays. |

Do not use a SELECT alias in the same query's WHERE or GROUP BY; repeat the expression or introduce a CTE. Use an outer query to filter window results:

```sql
WITH ranked AS (
    SELECT order_id, customer_id,
           row_number() OVER (PARTITION BY customer_id ORDER BY order_ts DESC, order_id DESC) AS rn
    FROM "AwsDataCatalog"."analytics"."orders"
)
SELECT order_id, customer_id FROM ranked WHERE rn = 1;
```

## Types, arithmetic and nested values

- Hive column DDL uses `STRING`, `BINARY`, `ARRAY<type>`, `MAP<key, value>` and `STRUCT<field:type>`. Query casts/CTAS expressions use `VARCHAR`, `VARBINARY`, `ARRAY(type)`, `MAP(key, value)` and `ROW(field type)` instead.
- Integer division truncates. Multiplying by `1.0` introduces a DECIMAL literal, not necessarily floating-point arithmetic. For approximate analytical ratios and means, cast **before** division/aggregation: `CAST(numerator AS DOUBLE) / NULLIF(denominator, 0)` and `AVG(CAST(amount AS DOUBLE))`.
- `AVG(DECIMAL)` can round to the input scale even without ROUND: averaging `DECIMAL '1.00'` and `DECIMAL '2.01'` returns `1.51`, whereas averaging their DOUBLE casts returns `1.505`. Likewise, `DECIMAL '30.00' / DECIMAL '180.00'` returns `0.17`. Casting those results afterward cannot recover the lost digits.
- Keep sums and exact monetary arithmetic as DECIMAL. If a fixed-scale mean/ratio is explicitly required, choose its operand scales deliberately instead of guessing ever-larger casts. Precision is at most 38, and `DECIMAL(p, s)` leaves only `p - s` integer digits: `DECIMAL(20,20)` cannot represent 15. Blindly widening every operand to `DECIMAL(38,s)` can overflow during type coercion or division. DOUBLE avoids fixed-scale rounding for analytical means, but is approximate.
- For a scalar extracted from JSON text, cast the VARCHAR before numeric/date comparisons. Do not apply `json_extract_scalar` to arrays or objects, and do not apply JSON parsing to an already typed ROW.
- `CROSS JOIN UNNEST` drops a parent with a NULL/empty array and multiple unnests can multiply rows. Use `LEFT JOIN UNNEST(items) AS u(item) ON TRUE` when such parents must survive.
- Specify NULL ordering when it matters, for example `ORDER BY amount DESC NULLS LAST`. Distinguish a NULL value from the text `'null'` or an empty string.

For an analytical summary, keep the total exact while computing the mean and count share in DOUBLE; do not start with AVG(amount) and try to repair its rounded output:

```sql
WITH totals AS (
    SELECT status, COUNT(*) AS n, SUM(amount) AS total_amount,
           AVG(CAST(amount AS DOUBLE)) AS mean_amount
    FROM orders GROUP BY status
)
SELECT status, n, total_amount, mean_amount,
       CAST(n AS DOUBLE) / NULLIF(SUM(n) OVER (), 0) AS order_share
FROM totals;
```

## Time and partition pruning

- Use typed `DATE '2026-01-01'` and `TIMESTAMP '2026-01-01 00:00:00'` literals. A plain TIMESTAMP has no time zone; do not silently interpret it as UTC. `from_iso8601_timestamp` parses an ISO instant with its zone; `AT TIME ZONE` expresses the required business zone.
- `from_unixtime` takes seconds. Convert millisecond epochs explicitly, for example `from_unixtime(epoch_ms / 1000.0)`; do not assume a BIGINT can be cast straight to TIMESTAMP.
- For a Hive table with both an event timestamp and a separate date partition, filter **both**. Athena does not infer their relationship. Match the partition's actual type, often STRING, and filter it directly rather than wrapping it in a cast/function.

```sql
SELECT customer_id, COUNT(*) AS event_count
FROM "AwsDataCatalog"."analytics"."events"
WHERE event_date >= '2026-01-01' AND event_date < '2026-02-01'
  AND event_ts >= TIMESTAMP '2026-01-01 00:00:00'
  AND event_ts < TIMESTAMP '2026-02-01 00:00:00'
GROUP BY customer_id;
```

Use these matching boundaries only when `event_date` represents that same time basis. Iceberg hidden partition transforms have different pruning behavior; inspect table metadata before applying Hive partition rules.

## Execution and storage boundaries

- Choose catalog, database, region and Workgroup through datasource/tool context. This adapter does not implement session SET/USE or explicit BEGIN/COMMIT/ROLLBACK transactions; submit individual statements. Iceberg mutations can still be atomic single statements.
- Source data and query results are different locations. A Workgroup may enforce its result location or use managed query results; do not override those settings in SQL or assume results always live in a caller-owned S3 prefix.
- Select only required columns and constrain actual partitions. `LIMIT` and the adapter's row cap bound returned rows, not scanned bytes. Use the Workgroup scan cutoff for a service-side scan budget; this is not an account-wide spending limit.
- A Glue table definition does not establish enforced keys, indexes or OLTP update behavior. Ordinary Hive external tables are not row-mutable; UPDATE/DELETE/MERGE require supported Iceberg tables. This adapter does not implement generic dataframe migration.

## Task reference

Read [S3 table DDL, CTAS, INSERT and Iceberg](references/table-design-and-writes.md) for the requested write or table-design operation, including SQL-only authoring. It covers the statement-family split, partition-column order, empty CTAS destinations, Workgroup restrictions and data ownership when dropping tables.

## Sources

- [Athena SQL reference](https://docs.aws.amazon.com/athena/latest/ug/ddl-sql-reference.html), [SELECT](https://docs.aws.amazon.com/athena/latest/ug/select.html), [data types](https://docs.aws.amazon.com/athena/latest/ug/data-types.html), [identifier quoting](https://docs.aws.amazon.com/athena/latest/ug/reserved-words.html).
- [Arrays](https://docs.aws.amazon.com/athena/latest/ug/accessing-array-elements.html), [JSON extraction](https://docs.aws.amazon.com/athena/latest/ug/extracting-data-from-JSON.html), [timestamps and partitions](https://docs.aws.amazon.com/athena/latest/ug/data-types-timestamps.html).
- Trino background for Athena-compatible expressions: [decimal arithmetic](https://trino.io/docs/current/functions/decimal.html), [aggregates](https://trino.io/docs/current/functions/aggregate.html). Athena engine support takes precedence over the latest Trino documentation.
