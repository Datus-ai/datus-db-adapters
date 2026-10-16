# Doris data loading

The target tables used in these examples are defined in [table design and writes](table-design-and-writes.md). Adapt their schemas and paths to the actual target.

## Data loading

### Stream Load — synchronous HTTP ingestion

```shell
curl --location-trusted -u <user>:<password> \
  -H "Expect:100-continue" \
  -H "label:orders_20260820_01" \
  -H "column_separator:," \
  -H "columns:order_id,order_time,user_id,amount,channel" \
  -T orders.csv \
  -XPUT http://<fe_host>:8030/api/demo/dwd_orders/_stream_load
```

```shell
# JSON array in one file
curl --location-trusted -u <user>:<password> \
  -H "Expect:100-continue" \
  -H "format:json" -H "strip_outer_array:true" \
  -H "jsonpaths:[\"$.order_id\",\"$.user_id\",\"$.amount\"]" \
  -H "columns:order_id,user_id,amount" \
  -T orders.json \
  -XPUT http://<fe_host>:8030/api/demo/dwd_orders/_stream_load
```

- Submitted over HTTP, never as a SQL clause. The endpoint is `/api/{db}/{table}/_stream_load` on the **FE HTTP port** (8030 by default, not the 9030 query port).
- The FE answers with a 307 redirect to a BE, so `--location-trusted` is required to carry credentials through it. That flag resends the credentials to whatever host the FE names, so submit only to an FE you trust; use `https://<fe_host>:8050` where the cluster sets `enable_https`, which is off by default and listens on its own port rather than 8030.
- The FE chooses the redirect target, so a BE address the client cannot route to is fixed at the FE, not by retrying. Give the BEs a client-reachable `tag.public_endpoint` or `tag.private_endpoint` and select it with `-H "redirect-policy: public"` or `-H "redirect-policy: private"` (Doris 3.1.0+); `direct` forces `be_host`, and with no header the FE tries `public_endpoint`, then `private_endpoint`, then `be_host`. Posting straight to a BE's HTTP port (8040 by default) using the same path is only an option when that BE is itself reachable from the client.
- Synchronous: the response body is the result. `"Status": "Success"` means committed; `"Publish Timeout"` means committed but not yet visible and needs no retry; `"Label Already Exists"` means that label already ran — check `ExistingJobStatus`; `"Fail"` means nothing was written. Inspect bad rows with `curl "<ErrorURL>"`.
- Reuse one `label` per logical batch to get at-most-once semantics on retry. Labels are kept for 3 days by default (`label_keep_max_second`).
- Common headers: `format` (`csv` default, plus `json`, `csv_with_names`, `parquet`, `orc`, `arrow`), `column_separator`, `line_delimiter`, `where`, `partitions`, `max_filter_ratio` (0 by default), `timeout` (600s default), `strict_mode`, `timezone`, `merge_type` with `delete`, `read_json_by_line`, `json_root`, `skip_lines`, `enclose`, `escape`.
- In CSV, `\N` is NULL and an empty span between delimiters is the empty string.
- A Stream Load cannot be cancelled by the user; it ends on success, error, or timeout. History is not recorded unless `enable_stream_load_record=true` is set in `be.conf`, after which `SHOW STREAM LOAD FROM <db>` lists finished jobs.
- Keep a single file under about 10 GB (`streaming_load_max_mb` on the BE, 10240 MB default); split larger inputs.

### INSERT INTO SELECT — synchronous SQL ingestion

Use it for in-Doris ETL, for pulling from an external catalog, and for importing files through a TVF.

```sql
-- From another Doris table, into the demo.dws_gmv_daily aggregate table
INSERT INTO demo.dws_gmv_daily (stat_date, category, gmv, order_cnt, buyers)
SELECT CAST(o.order_time AS DATE), p.category,
       SUM(oi.amount), COUNT(*), BITMAP_UNION(TO_BITMAP(o.user_id))
FROM demo.dwd_order_items oi
JOIN demo.dwd_orders   o ON oi.order_id   = o.order_id
JOIN demo.dim_products p ON oi.product_id = p.product_id
GROUP BY 1, 2;

-- From an external catalog, no pre-staging required
INSERT INTO demo.dim_users
SELECT user_id, name, risk_status, updated_at FROM mysql_ops.ops.users;

-- From files, via a table value function
DESC FUNCTION s3 (
    "uri" = "s3://bucket/path/orders_*.parquet",
    "s3.endpoint" = "https://s3.us-east-1.amazonaws.com",
    "s3.region" = "us-east-1",
    "s3.access_key" = "ak",
    "s3.secret_key" = "sk",
    "format" = "parquet"
);

INSERT INTO demo.dwd_orders (order_id, order_time, user_id, amount)
SELECT CAST(order_id AS BIGINT), CAST(order_time AS DATETIME), CAST(user_id AS BIGINT), amount
FROM s3 (
    "uri" = "s3://bucket/path/orders_*.parquet",
    "s3.endpoint" = "https://s3.us-east-1.amazonaws.com",
    "s3.region" = "us-east-1",
    "s3.access_key" = "ak",
    "s3.secret_key" = "sk",
    "format" = "parquet"
);
```

- TVFs available: `s3(...)` for S3-compatible object storage, `hdfs(...)`, `http(...)` (Doris 4.0.2+), `local(...)`, and the unified `file(...)` (Doris 3.1.0+). A TVF is a table and may appear in `FROM`, a CTE, or a join.
- Run `DESC FUNCTION <tvf>(...)` first to see the inferred schema. Parquet and ORC schemas come from file metadata; CSV and JSON are inferred from the first row and default to `string`, so cast explicitly or pass `csv_schema` as `'name1:type1;name2:type2'`. With multi-file matching, the first file's schema wins.
- Paths support `*`, `{1..10}`, and `{a,b,c}`. A path matching nothing returns an empty result set, and `DESC FUNCTION` then shows a single placeholder column `__dummy_col`.
- A `CREATE RESOURCE` of type `s3` or `hdfs` can be referenced as `"resource" = "<name>"` so credentials are not repeated in every statement; TVF properties override the resource's.
- Timeouts: FE `insert_load_default_timeout_second` and the session variable `insert_timeout`, both 4 hours by default. Size them as at least data volume divided by expected throughput.
- Inspect finished jobs with `SHOW LOAD FROM <db>` (`Type = INSERT`), and filtered rows with `SHOW LOAD WARNINGS ON "<url>"`. Wrap the statement in a `JOB` to run it asynchronously.

### Routine Load — continuous Kafka ingestion

```sql
CREATE ROUTINE LOAD demo.orders_stream ON dwd_orders
COLUMNS(order_id, order_time, user_id, amount)
PROPERTIES (
    "format" = "json",
    "jsonpaths" = "[\"$.order_id\",\"$.order_time\",\"$.user_id\",\"$.amount\"]",
    "desired_concurrent_number" = "3"
)
FROM KAFKA (
    "kafka_broker_list" = "kafka:9092",
    "kafka_topic" = "orders",
    "property.kafka_default_offsets" = "OFFSET_BEGINNING"
);

SHOW ROUTINE LOAD FOR demo.orders_stream;
PAUSE  ROUTINE LOAD FOR demo.orders_stream;
RESUME ROUTINE LOAD FOR demo.orders_stream;
STOP   ROUTINE LOAD FOR demo.orders_stream;
```

- For CSV topics, use `COLUMNS TERMINATED BY ","` before the `COLUMNS(...)` clause instead of the JSON properties.
- `ALTER ROUTINE LOAD` requires the job to be paused first, then resumed. `STOP` deletes the job irreversibly and it disappears from `SHOW ROUTINE LOAD`.
- One job spawns many subtasks; `SHOW ROUTINE LOAD` reports job-level state and lag, and the task view reports per-subtask consumption progress.
