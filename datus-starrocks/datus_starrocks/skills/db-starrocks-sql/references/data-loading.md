# StarRocks data loading

## Data loading capabilities

### Choose a loading method

- Prefer `INSERT INTO ... SELECT ... FROM FILES(...)` for ordinary one-off S3, GCS, or HDFS imports when the target version supports the file format. It also allows previewing and transforming the source with SQL before writing.
- Check the server version with `SELECT current_version()`: `FILES()` with Parquet requires 3.1.0+ (3.2+ for GCS), and CSV (including delimited `.txt` files) requires 3.3+. These capabilities remain available in 4.x; do not assume every 3.x release supports them. Verify other formats and optional properties against the target-version documentation.
- Preserve an explicitly requested loading method. Otherwise, treat `FILES()` as a default, not a requirement. Broker Load can be useful for asynchronous execution, format/version compatibility, or access through a different supported storage reader.
- Configure network access and any required credentials for the StarRocks cluster, not just the SQL client. Do not assume the client's AWS CLI or gcloud credentials, or SQL `${ENV_VAR}` placeholders, are automatically forwarded or expanded. Use placeholders in proposed SQL; resolve missing authentication without exposing secrets in the conversation.
- For native GCS access, pair `gs://bucket/object` with `gcp.gcs.*` properties. When using authentication, set `"gcp.gcs.use_compute_engine_service_account" = "true"` only for a confirmed bound GCP VM identity, or use `gcp.gcs.service_account_email`, `gcp.gcs.service_account_private_key_id`, and `gcp.gcs.service_account_private_key`. See the [GCS loading guide](https://docs.starrocks.io/docs/loading/objectstorage/gcs/) and [GCS authentication](https://docs.starrocks.io/docs/integrations/csp_auth/authenticate_to_gcs/).
- For GCS access through `FILES()` using StarRocks's S3-compatible reader, pair `s3://bucket/object` with `"aws.s3.endpoint" = "https://storage.googleapis.com"`, `"aws.s3.enable_path_style_access" = "true"`, and the supported `aws.s3.*` authentication settings. The [StarRocks 3.3 FILES() reference](https://github.com/StarRocks/starrocks/blob/3.3.22/docs/en/sql-reference/sql-functions/table-functions/files.md) also documents a legacy `s3a://` route with `fs.s3a.access.key`, `fs.s3a.secret.key`, and `fs.s3a.endpoint`. Match properties and endpoint format to the deployed version and reader; do not assume the property sets or anonymous-access capabilities are interchangeable. Both still access GCS, not AWS. Respect explicit protocol/authentication constraints and recheck previously successful SQL before changing its access route or parameters.
- Separate public-object permissions from client authentication behavior. Public HTTPS access does not prove a StarRocks route supports anonymous reads; documentation listing authenticated methods does not prove anonymity is impossible. Do not invent credentials or assume placeholder keys enable anonymous requests.

### Investigate loading failures

- Scope each failure to its stage, such as network access, credential lookup, authorization, parsing, or writing. Use the observed error, deployed capabilities, and target-version documentation to choose and test a plausible alternative within the user's constraints. Do not infer a product-wide limitation from one access path or repeat failed attempts without new evidence.
- Check the component that actually reads the data: native readers and separate Broker services may use different clients, authentication modes, and configuration properties. For public objects, investigate supported anonymous access before concluding that credentials or cluster changes are necessary. Distinguish supported per-job properties from global configuration; prefer existing capabilities over deployment changes.
- Prefer a read-only check where supported, or a minimal authorized load. Before retrying a write through any method, inspect its job/transaction and target state to avoid duplicates; resolve uncertain completion first. If no supported option remains within scope, report the observed blocker and ask for direction.

### FILES() import and validation

For execution requests, use the following workflow; for SQL-generation or plan-only requests, provide the SQL without running it and state any unverified access prerequisites instead of blocking on credential setup.

1. Confirm the exact source path, format, destination, and access mode (authenticated or anonymous). Put `"path"`, `"format"`, and the appropriate storage properties inside `FILES(...)`; for S3, include `"aws.s3.region"` and the chosen StarRocks-supported credential settings. Do not copy Broker Load's `WITH BROKER` clause into this function.
2. Preview with `SELECT ... FROM FILES(...) LIMIT ...` to check schema, casts, nulls, and parsing before writing. `DESC FILES(...)` is an optional schema inspection on 3.3.4+, not a prerequisite for earlier supported versions.
3. For delimited text, use `"format" = "csv"` regardless of a `.txt` suffix. Match `"csv.column_separator"` and `"csv.row_delimiter"` to the actual file, including CRLF when present; account for SQL/client escaping. Set `"csv.skip_header"` to the actual header count (zero for no header). Inspect positional columns such as `$1`, `$2`, and map them explicitly to target names with aliases and casts; skipping a header does not assign its names to columns.
4. Inspect an existing target before loading; do not silently drop, truncate, or append another copy. For a new target, create an explicit schema using the [table-design rules](table-design-and-writes.md), or use `CREATE TABLE ... AS SELECT ... FROM FILES(...)` when inferred types are appropriate. For single-BE local tests, set `"replication_num" = "1"`; otherwise follow the deployment's replication policy instead of copying this test setting.
5. Load into an existing table with `INSERT INTO database.table (target_columns...) SELECT source_expressions... FROM FILES(...)`. This is normally synchronous; report errors or unconfirmed completion, and inspect the INSERT job/transaction status before retrying a timeout. Repeating an INSERT into a Duplicate Key table can duplicate rows.
6. After the data is visible, validate target row counts, relevant distinct keys, nulls, and representative aggregates against known source expectations. Account for pre-existing rows and the target table's append/upsert/aggregation semantics. For text, check for residual carriage returns or shifted columns. Distinguish successful execution from these data checks, and do not claim an import passed when only its SQL was generated.

Consult the official [S3 loading guide](https://docs.starrocks.io/docs/integrations/streaming/pipe/s3/) for method selection and the [FILES() reference](https://docs.starrocks.io/docs/sql-reference/sql-functions/table-functions/files/) for format, version, and authentication parameters.

### Other loading methods and job status

- Use `LOAD LABEL database.label (...) WITH BROKER ... PROPERTIES (...)` for asynchronous Broker Load from HDFS or cloud storage. `SHOW BROKER` exposes deployed Broker availability; `WITH BROKER "<name>"` selects that service and its supported filesystem properties. Brokerless access retains `WITH BROKER` without a name and uses the native reader's storage properties. Do not assume the two modes share authentication capabilities or accept interchangeable parameters.
- Use Stream Load through the HTTP API for synchronous request-oriented ingestion; do not represent HTTP headers as SQL clauses.
- Use Routine Load for a long-running Kafka ingestion job and manage it with the Routine Load SQL commands.
- Use `information_schema.loads` for Broker Load and INSERT job status on StarRocks 3.1+, or `SHOW LOAD [FROM database]` where appropriate. Use `SHOW ROUTINE LOAD` for Routine Load jobs.
- Use `CANCEL LOAD ... WHERE LABEL = ...` to cancel an eligible asynchronous load job.
- Treat successful Broker Load submission as job acceptance, not proof that rows are committed. Expose label, status, progress, error, and cancellation capabilities without imposing a polling workflow.

### Loading SQL examples

These are alternative syntax examples, not sequential loads or a required fallback order. They assume an existing `target_db.target_table(item_id BIGINT, item_name VARCHAR(100))` and a two-column, comma-delimited CSV with one header row and LF line endings. Replace identifiers and `<...>` placeholders, and adapt the format and authentication to the actual source. For CRLF files, use `\r\n` instead of `\n`, accounting for SQL/client escaping.

#### INSERT INTO ... SELECT ... FROM FILES()

Authenticated S3 example (CSV requires StarRocks 3.3+). Preview the `SELECT` before inserting. The access-key properties illustrate one authentication method; use the supported settings for the chosen identity instead when appropriate.

```sql
INSERT INTO target_db.target_table (item_id, item_name)
SELECT CAST($1 AS BIGINT), CAST($2 AS VARCHAR(100))
FROM FILES(
    "path" = "s3://<bucket>/<prefix>/data.csv",
    "format" = "csv",
    "csv.column_separator" = ",",
    "csv.row_delimiter" = "\n",
    "csv.skip_header" = "1",
    "aws.s3.region" = "<region>",
    "aws.s3.access_key" = "<access_key>",
    "aws.s3.secret_key" = "<secret_key>"
);
```

Storage and CSV properties belong inside `FILES(...)`; the `SELECT` maps source positions to the explicit INSERT column list. For a supported S3-compatible endpoint, add `aws.s3.endpoint` and, when needed, `aws.s3.enable_path_style_access`; verify its authentication requirements independently of AWS S3.

#### Broker Load through a deployed Broker

Public S3-compatible object example using a Broker with Hadoop S3A support. Select an available Broker and verify that its deployed client supports the chosen credential provider. The anonymous provider below sends unsigned requests; it is not a fake access key. An authenticated object requires a different provider and appropriate credentials.

```sql
LOAD LABEL target_db.load_csv_unique_label
(
    DATA INFILE ("s3a://<bucket>/<prefix>/data.csv")
    INTO TABLE target_table
    COLUMNS TERMINATED BY ","
    ROWS TERMINATED BY "\n"
    FORMAT AS "CSV"
    (skip_header = 1)
    (item_id, item_name)
)
WITH BROKER "<broker_name>"
(
    "fs.s3a.endpoint" = "https://<s3-compatible-endpoint>",
    "fs.s3a.path.style.access" = "true",
    "fs.s3a.aws.credentials.provider" = "org.apache.hadoop.fs.s3a.AnonymousAWSCredentialsProvider"
)
PROPERTIES
(
    "timeout" = "600",
    "strict_mode" = "true",
    "max_filter_ratio" = "0"
);

SHOW LOAD FROM target_db WHERE LABEL = 'load_csv_unique_label';
```

CSV format options follow `FORMAT AS "CSV"` and precede the column list; `skip_header = 1` uses an unquoted integer, not a `PROPERTIES` block. The final `PROPERTIES` block controls the load job. Use a unique label for each intended batch and confirm `FINISHED` plus data validation before reporting success.

The same named-Broker S3A route can access GCS through its S3-compatible XML endpoint, `https://storage.googleapis.com`: use `s3a://` for that interface, not `gs://`. Anonymous public GCS CSV loading through this route was verified on StarRocks 3.3.22 with a Broker using Hadoop S3A 3.4.1; verify the deployed Broker's capabilities rather than assuming every deployment has the same client. This is distinct from native `gs://` access. Here, `fs.s3a.*` configures the named Broker's client. Recheck which properties reach the reader before adapting this example to `FILES()` or brokerless loading; removing the Broker name does not preserve that client or its anonymous provider.

For access-key authentication, one S3A option is `org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider` with `fs.s3a.access.key` and `fs.s3a.secret.key`, replacing the anonymous provider. When using access-key authentication against GCS's XML API (including these S3-compatible `FILES()` routes), use [GCS HMAC credentials](https://docs.cloud.google.com/storage/docs/interoperability), not AWS-issued keys or a service-account JSON private key; public objects read through a supported anonymous provider do not require these keys. See [S3A authentication providers](https://hadoop.apache.org/docs/r3.4.1/hadoop-aws/tools/hadoop-aws/index.html#Changing_Authentication_Providers) for provider choices and [Broker Load syntax](https://docs.starrocks.io/docs/sql-reference/sql-statements/loading_unloading/BROKER_LOAD/) for clause placement; verify against the deployed versions.
