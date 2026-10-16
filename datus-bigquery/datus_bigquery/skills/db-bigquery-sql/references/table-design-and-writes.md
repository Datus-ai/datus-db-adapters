# BigQuery DDL and migration

## DDL and migration

- Use BigQuery types such as `INT64`, `FLOAT64`, `NUMERIC`, `BIGNUMERIC`, `BOOL`, `STRING`, `BYTES`, `JSON`, `ARRAY`, and `STRUCT`.
- BigQuery has no `AUTO_INCREMENT`, `SERIAL`, storage `ENGINE`, `DISTRIBUTED BY`, or `BUCKETS` clause.
- Declare primary and foreign keys as `NOT ENFORCED`.
- Put `PARTITION BY` and then `CLUSTER BY` after the column list:

```sql
CREATE TABLE `my-project.analytics.events` (
  event_id INT64 NOT NULL,
  event_ts TIMESTAMP,
  attributes JSON,
  PRIMARY KEY (event_id) NOT ENFORCED
)
PARTITION BY DATE(event_ts)
CLUSTER BY event_id;
```
