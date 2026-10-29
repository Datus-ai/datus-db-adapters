# Athena table design and writes

Determine the catalog, table format, existing DDL, S3 location and Workgroup result policy before choosing a write statement. Ordinary Glue/Hive external tables and Iceberg tables have different mutation and deletion semantics.

## Hive external table DDL

`CREATE EXTERNAL TABLE` registers metadata over an S3 prefix; it does not copy rows into a database server. Declare the file format/SerDe that matches the actual objects. Do not invent primary/foreign-key enforcement, indexes, AUTO_INCREMENT, temporary tables or stored procedures.

Hive DDL uses backticks and Hive types. The same columns use double quotes and Trino types when queried:

```sql
CREATE EXTERNAL TABLE analytics.orders_archive (
    `order` BIGINT,
    customer_id STRING,
    amount DECIMAL(18, 2),
    event_ts TIMESTAMP
)
PARTITIONED BY (event_date STRING)
STORED AS PARQUET
LOCATION 's3://example-data/orders-archive/';
```

Query the reserved column as `SELECT "order" FROM "analytics"."orders_archive"`. Partition columns are declared separately from file columns. Register existing Hive partitions with `ALTER TABLE ... ADD IF NOT EXISTS PARTITION (...) LOCATION 's3://.../'`, or use the table's configured partition projection. Do not assume an S3 directory is automatically a Glue partition. `MSCK REPAIR TABLE` discovers Hive-style partitions but does not remove stale ones.

For an ordinary external Hive table, DROP TABLE removes catalog metadata and leaves S3 objects. Do not describe DROP TABLE as a way to erase the source data. Iceberg DROP TABLE can delete the table's data as well as metadata; inspect the table format rather than generalizing the external-Hive behavior.

## CTAS writes files

CTAS follows the Trino query/type conventions. This example applies to a Hive destination and requires a Workgroup that permits an explicit destination:

```sql
CREATE TABLE "analytics"."daily_orders"
WITH (
    format = 'PARQUET',
    external_location = 's3://example-data/daily-orders-new-run/',
    partitioned_by = ARRAY['event_date']
) AS
SELECT customer_id, SUM(amount) AS amount, event_date
FROM "analytics"."orders_archive"
WHERE event_date = '2026-01-01'
GROUP BY customer_id, event_date;
```

- Keep the property name `format` lowercase. Project destination partition columns last in the SELECT list.
- An explicit `external_location` must be empty. Athena does not clear old files or safely overwrite an earlier attempt; failed CTAS can leave objects behind. Inspect the query manifest/location before retrying, and choose a fresh destination when appropriate.
- CTAS with `external_location` fails under a Workgroup that enforces the query-result location. Respect the configured policy and omit the override when appropriate; do not silently switch Workgroups to bypass it.
- Iceberg CTAS instead uses `table_type = 'ICEBERG'`, `is_external = false`, and `location = 's3://.../'`. Do not use the Hive `external_location` property as the Iceberg root location.

## INSERT and partitions

INSERT INTO appends files; it is not an overwrite or an upsert. Repeating a successful append can duplicate rows. Choose explicit projected columns rather than blindly copying SELECT * across different schemas. For a partitioned destination, put its partition columns last in the projection.

CTAS and INSERT INTO have a limit of 100 destination partitions per statement. Split larger writes into disjoint partition ranges. A small LIMIT on input rows does not prove the destination touches at most 100 partitions. Honor format-specific write restrictions; support for reading a format does not imply support for inserting into it.

## Iceberg mutations and engine scope

In Athena SQL engine v3, supported Iceberg tables allow UPDATE, DELETE FROM and MERGE INTO. Ordinary Hive tables do not gain these operations merely because their files are Parquet. Verify Iceberg metadata and source/target keys before authoring a merge.

Single Iceberg mutations create snapshots with their own transaction semantics; this does not provide a multi-statement BEGIN/COMMIT session through this adapter. Athena uses merge-on-read for UPDATE/DELETE/MERGE and ignores copy-on-write properties for those operations. Generic TRUNCATE, INSERT OVERWRITE and CREATE OR REPLACE TABLE are not portable substitutes; verify the exact Athena statement and table format instead.

## Sources

- [CREATE TABLE](https://docs.aws.amazon.com/athena/latest/ug/create-table.html), [MSCK REPAIR TABLE](https://docs.aws.amazon.com/athena/latest/ug/msck-repair-table.html), [DROP TABLE](https://docs.aws.amazon.com/athena/latest/ug/drop-table.html).
- [CTAS](https://docs.aws.amazon.com/athena/latest/ug/create-table-as.html), [CTAS considerations](https://docs.aws.amazon.com/athena/latest/ug/ctas-considerations-limitations.html), [INSERT INTO](https://docs.aws.amazon.com/athena/latest/ug/insert-into.html).
- [Iceberg data changes](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg-updating-iceberg-table-data.html), [Iceberg DROP TABLE](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg-drop-table.html), [MERGE INTO](https://docs.aws.amazon.com/athena/latest/ug/merge-into-statement.html).
