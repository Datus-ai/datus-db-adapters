# datus-athena

Amazon Athena adapter using the official boto3 SDK. Python 3.12 or later.

```bash
pip install datus-athena
```

## Configuration

```python
from datus_athena import AthenaConfig, AthenaConnector

with AthenaConnector(AthenaConfig(
    region="us-east-1",
    workgroup="datus-test",
    catalog="AwsDataCatalog",
    database="analytics",
)) as connector:
    result = connector.execute_query("SELECT '数据' AS message", "list")
    assert result.success, result.error
    print(result.sql_return, result.query_execution_id, result.execution_info)
```

Datus Agent discovers the `datus.adapters` entry point. Adapter fields pass
through `DbConfig.extra`; no Agent database-name branch is needed:

```yaml
services:
  datasources:
    lake:
      type: athena
      aws:
        region: us-east-1
        # profile: datus-role   # optional standard AWS role profile
      workgroup: datus-test
      catalog: AwsDataCatalog
      database: analytics
      query_timeout_seconds: 600
      max_rows: 10000
```

The namespace is `catalog.database.table`. There is no additional Datus schema
level. Per-call `catalog_name` and `database_name` override defaults without
changing another thread/task's context. SELECT identifiers use double quotes;
Hive table DDL uses backticks. The registered SQLGlot dialect is `athena`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `region` | SDK configuration | AWS region |
| `profile_name` | SDK default chain | Named profile, including AssumeRole profiles |
| `ca_bundle` | SDK trust configuration | Optional trusted CA bundle; verification stays enabled |
| `workgroup` | `primary` | Athena SQL Workgroup |
| `catalog` | `AwsDataCatalog` | AWS Glue data catalog |
| `database` | unset | Default database |
| `output_location` | unset | Optional `s3://bucket/prefix/` for query results |
| `timeout_seconds` | 30 | Each SDK connection/socket read timeout |
| `query_timeout_seconds` | 600 | Foreground query wait deadline |
| `result_timeout_seconds` | 60 | Result pagination deadline |
| `cancel_timeout_seconds` | 10 | Wait after native cancellation; zero requests only |
| `poll_interval_seconds` | 1 | State polling interval |
| `max_rows` | 10000 | Maximum returned rows |
| `max_result_bytes` | 16777216 | Maximum retained UTF-8 cell bytes before type conversion |
| `page_size` | 1000 | Result API rows per response, including any header |

## AWS identity and permissions

Credentials use the SDK default provider chain (environment, shared profiles,
SSO, web identity, container or instance role). No access keys or short-lived
tokens are stored in this adapter's configuration. Explicit temporary credentials
in environment variables expire when their upstream issuer says they expire;
use a refreshable provider for long-lived processes.

For cross-account access, configure a standard AWS profile and set
`profile_name="datus-role"`:

```ini
# ~/.aws/config; the source profile's credentials remain outside agent.yml
[profile datus-role]
role_arn = arn:aws:iam::123456789012:role/DatusAthenaReader
source_profile = development
region = us-east-1
```

Boto3 owns AssumeRole caching and refresh. This package uses the shared
`datus-db-core>=0.1.8` `AWSIdentity` for Session/client ownership, timeouts, retries
and cleanup. The AWS connection fields are `region`, `profile_name`, and
`ca_bundle`. The shared CORE-01 shape
`aws: {region: ..., profile: ..., ca_bundle: ...}` is also accepted and normalized
to these fields; conflicting nested/flat values fail configuration validation.
An existing `boto3.Session` can also be passed as `session=`.

Grant the test identity the scoped Athena actions used here:
`GetWorkGroup`, `ListDataCatalogs`, `ListDatabases`,
`GetDatabase`, `ListTableMetadata`, `GetTableMetadata`, `StartQueryExecution`,
`GetQueryExecution`, `GetQueryResults`, and `StopQueryExecution`.
Also grant the required Glue database/table access and source S3 reads. S3
results require access to the result prefix, including `s3:GetObject` and query
result writes. Encrypted data/results require the corresponding KMS permissions;
Lake Formation protected data requires its grants as well as IAM access.
Managed results use Workgroup-scoped Athena result permissions and the managed
results KMS key policy when configured. Metadata access alone does not prove
query/data access: `test_connection()` performs both checks and a small SELECT.

## Query behavior and support boundary

Each submission reads the Workgroup configuration. Managed results and enforced
Workgroup settings take precedence: the adapter omits client output overrides.
Otherwise `output_location` is passed only when explicitly supplied. A Workgroup
with its own S3 result location needs no client output location. Both result
modes use `GetQueryResults`; the adapter does not download complete S3 files.

Queries return an `AthenaExecuteSQLResult`, a subclass of `ExecuteSQLResult`.
`query_execution_id` identifies the native query; `execution_info` includes state,
Athena error details, engine version, scan/timing statistics and truncation.
List results preserve NULL, Decimal, dates, timestamps and Chinese text. Complex
types, timezone timestamps, and high-precision timestamp signatures stay as text;
no lossy complex-type parsing is attempted. A plain `timestamp` value with more
than six fractional digits fails with a `CAST(... AS VARCHAR)` directive rather
than silently losing precision. Duplicate column names require aliases
for list output; positional Arrow/pandas/CSV results retain them.

Result row/byte budgets stop pagination and set `truncated` and
`truncation_reason`. The byte budget counts retained cell text, not HTTP response
bytes or Python object overhead; one SDK response is still received in full.
Query and pagination deadlines are checked between SDK calls; socket timeouts
and up to three SDK attempts can extend wall-clock completion beyond a deadline.
Neither a result cap nor SQL LIMIT is a scan-cost guarantee. Set Workgroup
per-query scanned-byte controls and service-side limits before running queries.

On wait timeout or polling failure, the adapter requests `StopQueryExecution`,
then polls for a bounded period. `cancellation_requested` and
`cancellation_confirmed` are separate; a running query after cancellation wait
is reported with its last state and ID. Failed queries are not resubmitted even
when Athena marks them retryable. One UUID token is generated per logical
execution and reused by SDK request retries. `execute_query(...,
client_request_token=...)` permits explicit retry identity; the caller must reuse
the same SQL/context with a token. Cancellation is also available through
`cancel_query(query_execution_id)`.

Glue Catalog queries, tables, views, partition columns, SHOW CREATE metadata and
bounded sampling are implemented. DDL and DML are sent unchanged to Athena;
availability depends on table format and catalog permissions (UPDATE/DELETE/MERGE
require supported Iceberg tables). Generic dataframe migration is explicitly
unsupported. Transactions, session SET/USE, Spark Workgroups, federation-specific
features and durable cross-process task recovery are outside this release.

## Testing

Install from this checkout in a dedicated environment:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ../datus-db-core -e '.[test]'
.venv/bin/python -m pytest tests/unit -m 'not integration'
.venv/bin/python -m pytest tests/integration -m integration
```

Unit tests use real botocore `Stubber` responses, never AWS network access.
The cloud target remains disabled until CI-specific credentials and its workflow
are configured. No paid LocalStack dependency is required.

For real AWS validation, provision a dedicated SQL Workgroup with a per-query
scan cutoff and either an S3 result prefix or managed results, a disposable Glue
database, and a dedicated S3 data prefix. Set `AWS_REGION`, `ATHENA_WORKGROUP`,
`ATHENA_DATABASE`, `ATHENA_DATA_LOCATION`, and optionally `AWS_PROFILE` and
`ATHENA_OUTPUT_LOCATION`. `ATHENA_DATA_LOCATION` must be a dedicated empty
`s3://bucket/prefix/`; do not reuse application data locations.

For the smallest local-CLI environment in `us-east-1`, use the three-resource
[CloudFormation setup and cleanup guide](AWS_TESTING.md). It reuses one Workgroup
for S3 and managed-results acceptance, with a 10 MiB per-query scan cutoff.

```bash
# Creates five tiny Parquet TPC-H tables and a view from shared fixtures. Run once.
.venv/bin/python scripts/init_tpch_data.py
# Cloud tests query the prepared fixtures; no fixture initialization is skipped on error.
.venv/bin/python -m pytest tests/integration -m integration
# Removes only fixture table definitions. S3 objects remain for operator-controlled cleanup.
.venv/bin/python scripts/init_tpch_data.py --drop
```

Run the suite separately against S3-results and managed-results Workgroups.
Set `ATHENA_EXPECT_MANAGED_RESULTS=true` or `false` to assert the storage mode.
CTAS fixture initialization uses explicit data locations: prepare fixtures with
a Workgroup that does not enforce its result location, then run read-only
acceptance with enforced Workgroups against the same database. Athena rejects
CTAS `external_location` under an enforced result location
([AWS CTAS restrictions](https://docs.aws.amazon.com/athena/latest/ug/create-table-as.html)).
Without required environment variables, cloud fixtures skip with their names;
with those variables present, failed authentication/readiness/queries fail tests.
The environment owner must remove the dedicated S3 fixture/output prefix and
database after acceptance; the script does not delete buckets or arbitrary data.

Local AWS acceptance in `us-east-1` on 2026-10-16 passed with SQL engine v3:
28 short integration tests passed in enforced S3-results mode and 28 in
managed-results mode, with zero skipped. The actual STS-expiry case separately
passed after 15 minutes 29 seconds. These include scoped IAM denial, source KMS
and Lake Formation allow/deny controls, native cancellation, result/wait deadlines,
result budgets and controlled loopback TLS transport faults with real AWS queries.
See [environment setup, limits and validation](AWS_TESTING.md). CI credentials
and its workflow remain unassigned; the local identity is not copied to CI.

References: [Athena API](https://docs.aws.amazon.com/boto3/latest/reference/services/athena.html),
[SDK credential providers](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html),
[managed results](https://docs.aws.amazon.com/athena/latest/ug/managed-results.html).
