# Minimal AWS test environment

Use the local AWS CLI in `us-east-1`. The CloudFormation template creates only
one S3 bucket, one Glue database and one Athena SQL engine v3 Workgroup. SQL uses
on-demand scanned-data billing. The scan cutoff is 10 MiB per query. There is no
capacity reservation, Spark session, Glue ETL job, crawler, customer KMS key or
database server. S3 requests/storage and Glue catalog usage are billed separately.
See [Athena pricing](https://aws.amazon.com/athena/pricing/) and
[scan controls](https://docs.aws.amazon.com/athena/latest/ug/workgroups-setting-control-limits-cloudwatch.html).

The private bucket uses SSE-S3 and blocks public access. Query results expire
after one day; fixture data expires after seven days. Lifecycle expiration does
not delete the stack or Glue definitions. Recreate fixtures under a fresh empty
prefix after expiration. There is no automatic account-wide spending cutoff.

## Create

Run from `datus-athena/` after installing this checkout's packages. The existing
repository-root virtual environment is referenced as `../.venv`; substitute the
package-local `.venv` if following the README's installation commands.

```bash
export AWS_PROFILE=default
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
aws sts get-caller-identity

export ATHENA_TEST_RUN_ID="$(date -u +%Y%m%d-%H%M%S)-$(../.venv/bin/python -c 'import secrets; print(secrets.token_hex(2))')"
export ATHENA_TEST_STACK="datus-athena-test-$ATHENA_TEST_RUN_ID"
export ATHENA_DATABASE="datus_athena_${ATHENA_TEST_RUN_ID//-/_}"

aws cloudformation create-stack \
  --stack-name "$ATHENA_TEST_STACK" \
  --template-body file://scripts/aws-test-environment.yaml \
  --parameters "ParameterKey=RunId,ParameterValue=$ATHENA_TEST_RUN_ID" \
    "ParameterKey=DatabaseName,ParameterValue=$ATHENA_DATABASE" \
  --tags Key=Purpose,Value=datus-athena-acceptance "Key=RunId,Value=$ATHENA_TEST_RUN_ID" \
  --timeout-in-minutes 10 --on-failure DELETE
aws cloudformation wait stack-create-complete --stack-name "$ATHENA_TEST_STACK"

export ATHENA_TEST_BUCKET="$(aws cloudformation describe-stacks \
  --stack-name "$ATHENA_TEST_STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue | [0]" --output text)"
export ATHENA_WORKGROUP="datus-athena-test-$ATHENA_TEST_RUN_ID"
export ATHENA_CATALOG=AwsDataCatalog
export ATHENA_DATA_LOCATION="s3://$ATHENA_TEST_BUCKET/fixtures/"
export ATHENA_EXPECT_MANAGED_RESULTS=false
unset ATHENA_OUTPUT_LOCATION

../.venv/bin/python scripts/init_tpch_data.py
../.venv/bin/python -m pytest tests/integration -m integration -q
```

The Workgroup initially allows CTAS `external_location`; the fixture initializer
needs that setting and an empty S3 prefix. Do not initialize under an enforced
result location ([AWS CTAS restrictions](https://docs.aws.amazon.com/athena/latest/ug/create-table-as.html)).
The initializer creates the five tiny shared TPC-H tables and one view, without
copying production data. No credentials are written to repository files.

## Reuse the same Workgroup for result-mode acceptance

Run these steps sequentially, with no other test run using the Workgroup.
Enforce its S3 result location after fixture initialization:

```bash
aws athena update-work-group --work-group "$ATHENA_WORKGROUP" \
  --configuration-updates '{"EnforceWorkGroupConfiguration":true}'
ATHENA_EXPECT_MANAGED_RESULTS=false \
  ../.venv/bin/python -m pytest tests/integration -m integration -q
```

Switch to managed results, using the default AWS owned key:

```bash
aws athena update-work-group --work-group "$ATHENA_WORKGROUP" \
  --configuration-updates '{"EnforceWorkGroupConfiguration":true,"ResultConfigurationUpdates":{"RemoveOutputLocation":true,"RemoveEncryptionConfiguration":true},"ManagedQueryResultsConfigurationUpdates":{"Enabled":true}}'
ATHENA_EXPECT_MANAGED_RESULTS=true \
  ../.venv/bin/python -m pytest tests/integration -m integration -q
```

Restore the template's S3 configuration after acceptance. This keeps the same
minimal resources and permits future fixture initialization:

```bash
aws athena update-work-group --work-group "$ATHENA_WORKGROUP" \
  --configuration-updates "{\"EnforceWorkGroupConfiguration\":false,\"ManagedQueryResultsConfigurationUpdates\":{\"Enabled\":false},\"ResultConfigurationUpdates\":{\"OutputLocation\":\"s3://$ATHENA_TEST_BUCKET/query-results/\",\"EncryptionConfiguration\":{\"EncryptionOption\":\"SSE_S3\"}}}"
export ATHENA_EXPECT_MANAGED_RESULTS=false
```

Managed query results are available for 24 hours. Source fixture data still lives
in the test bucket. See [managed results](https://docs.aws.amazon.com/athena/latest/ug/managed-results.html).
Optional denied-identity and KMS/Lake Formation cases require their separately
documented environment variables; this minimal stack does not create IAM roles,
KMS keys or Lake Formation grants. Their skips do not mean those behaviors passed.

To include creation, insertion, metadata, typed retrieval and cleanup through
the shared `TableContractCase`, set
`ATHENA_WRITABLE_DATA_LOCATION="s3://$ATHENA_TEST_BUCKET/contracts/"`.
Grant scoped Glue table creation/deletion and S3 list/read/write/delete access.
The test creates one UUID-named external Parquet table and removes both its
definition and its private child prefix, including files written before a
failed assertion. Missing this optional variable skips only the writable case;
service or cleanup errors after configuration fail the test. It uses explicit
CREATE EXTERNAL TABLE and INSERT rather than CTAS, so it does not require
disabling Workgroup-enforced result settings.

For extended acceptance, provide isolated test resources and SDK profiles. Do not
change account-wide Lake Formation defaults or apply policies to existing users:

- `ATHENA_DENIED_PROFILE`: a valid AssumeRole profile with discovery permissions
  and an explicit `athena:StartQueryExecution` deny on the test Workgroup.
- `ATHENA_ROLE_PROFILE`: an allowed reader profile with `duration_seconds=900`.
  Set `ATHENA_RUN_ROLE_EXPIRY_TEST=1` for the 15-minute real expiry case. The test
  keeps its client/Session, proves the old token is rejected by STS, and verifies
  that the provider renews credentials for a successful subsequent query.
- `ATHENA_RESTRICTED_PROFILE`: the same query/data scope without the protected
  fixtures' KMS decrypt and Lake Formation SELECT grants.
- `ATHENA_KMS_SQL` and `ATHENA_LAKEFORMATION_SQL`: each must select `r_regionkey`
  from a dedicated copy of `tpch_region`, ordered and limited to row 0. The allowed
  role must return `[{"r_regionkey": 0}]`; the restricted role must be rejected.
  Use a customer KMS key only for the KMS fixture. Register only the LF fixture
  prefix; revoke IAMAllowedPrincipals on that table, grant SELECT to the allowed
  role and only DESCRIBE to the restricted role. LF can hide inaccessible columns
  and report a column authorization error rather than naming Lake Formation.
- `ATHENA_EXPECT_FAILURE_SQL`: an additional denied-data query. Set
  `ATHENA_FAILURE_PROFILE` when it needs a different profile from the main suite.
- `ATHENA_RUN_NETWORK_FAULT_TESTS=1`: requires OpenSSL. A loopback proxy generates
  a temporary trusted certificate for the Athena hostname, forwards signed HTTPS
  requests to real AWS, and injects one HTTP throttle, a lost submission response,
  or socket read timeouts. Upstream TLS verification stays enabled. These are
  controlled transport faults; they do not induce a real AWS service quota breach.

Run short cases separately from the real-expiry case, using a private
`AWS_CONFIG_FILE` rather than editing the workstation's shared config:

```bash
ATHENA_RUN_NETWORK_FAULT_TESTS=1 ../.venv/bin/python -m pytest tests/integration \
  -m integration -k 'not real_sts_expiry' -q
ATHENA_RUN_ROLE_EXPIRY_TEST=1 ../.venv/bin/python -m pytest \
  tests/integration/test_aws_authentication.py::test_cached_client_refreshes_after_real_sts_expiry -q
```

After extended testing, revoke the fixture's LF grants, deregister its prefix,
delete the two copied tables/objects and the disposable roles, and schedule the
customer KMS key for deletion with the minimum seven-day waiting period. Run the
full cleanup below after acceptance is complete. A pending-deletion key cannot be used;
see [KMS key deletion](https://docs.aws.amazon.com/kms/latest/developerguide/deleting-keys.html).

## Existing local environment

For an environment prepared in this checkout, source `.aws-test/env.sh` instead
of creating another stack. The ignored `.aws-test/` directory records resource
names, CLI requests, query IDs/statistics and test reports, without AWS credentials.
Check stack status and fixture age before reusing it.
The environment recorded on 2026-10-16 has since been fully decommissioned;
its local `env.sh` is disabled. Recreate the stack before future cloud tests.

```bash
source .aws-test/env.sh
aws cloudformation describe-stacks --stack-name "$ATHENA_TEST_STACK" \
  --query 'Stacks[0].StackStatus' --output text
../.venv/bin/python -m pytest tests/integration -m integration -q
```

## Cleanup

Run only for the dedicated environment above. First remove fixture definitions,
then verify the bucket belongs to this stack before emptying it. The template
has no bucket versioning. Deleting a nonempty bucket through CloudFormation fails.

```bash
../.venv/bin/python scripts/init_tpch_data.py --drop
owned_bucket="$(aws cloudformation describe-stack-resource \
  --stack-name "$ATHENA_TEST_STACK" --logical-resource-id TestBucket \
  --query 'StackResourceDetail.PhysicalResourceId' --output text)"
test -n "$ATHENA_TEST_BUCKET" && test "$owned_bucket" = "$ATHENA_TEST_BUCKET" && \
  aws s3 rm "s3://$ATHENA_TEST_BUCKET/" --recursive --only-show-errors && \
  aws cloudformation delete-stack --stack-name "$ATHENA_TEST_STACK" && \
  aws cloudformation wait stack-delete-complete --stack-name "$ATHENA_TEST_STACK"
```

## Validation record: 2026-10-16

This checkout used the local CLI `default` identity in `us-east-1`, Python
3.12.10, boto3/botocore 1.43.114, and Athena SQL engine v3. The stack reached
`CREATE_COMPLETE` with exactly the three declared resources. Fixture initialization
created five Parquet objects totaling 5,373 bytes and one Glue view. The Workgroup
was restored to non-enforced S3 output after testing; the 10 MiB cutoff remains.

| Check | Result |
| --- | --- |
| Unit tests | 111 passed; 98% source coverage |
| Enforced S3 results, extended suite | 28 passed, zero skipped, 195.17 seconds; real-expiry case deselected and run separately |
| Managed results, same Workgroup | 28 passed, zero skipped, 203.32 seconds; real-expiry case run separately |
| Actual STS expiry and renewal | 1 passed, 929.80 seconds; AWS rejected the expired static token, and the same client/Session renewed credentials for a successful query |
| IAM denial | Valid denied role could discover the catalog; StartQueryExecution was denied before a query ID was created |
| Source KMS and Lake Formation | Allowed/restricted roles queried the same copied fixtures; successful controls proved table/column existence before denial assertions |
| Wait/result deadlines and budgets | Actual AWS wait cancellation, result read deadline, row/byte truncation metadata passed |
| Transport faults | Controlled HTTP 429, a lost submission response, and socket read timeouts; retry/recovery passed against real AWS |
| Native idempotency | Retried HTTP submissions retained one token and one actual QueryExecutionId |
| Formatting and lint | Ruff lint/format and whitespace checks passed |

The extended matrix used four temporary scoped IAM roles, one temporary customer
KMS key, two copied fixture tables/objects and one LF-registered prefix. Account
Lake Formation defaults and existing IAM user policies were not changed. The LF
table was verified as registered; its restricted identity had DESCRIBE without
SELECT, so AWS hid inaccessible columns and returned an authorization diagnostic.
The KMS object was verified as SSE-KMS using the test key; only the allowed role
had decrypt permission. No stored access keys or global AWS config changes were
required. Test profiles and reports stayed in the ignored `.aws-test/` directory.

Cleanup removed all four temporary roles, both protected tables/objects and the
LF prefix registration. The test KMS key is `PendingDeletion` with a seven-day
waiting period. A subsequent full teardown on 2026-10-16 reached
`DELETE_COMPLETE`: the original bucket (328 objects), database and Workgroup
were deleted. Account LF settings were verified unchanged. The final query audit found no
active queries and confirmed that the client output override was ignored.
These zero-skip results used the temporary extended environment. Future cloud
runs require the environment and any optional permission/role profiles to be
recreated. The writable table contract added on 2026-10-19 has offline SDK and
S3 cleanup coverage, but has not been run against AWS after this teardown and
is not included in the historical counts above.

The network tests inject faults at a local TLS proxy while forwarding other
signed requests to real AWS with normal upstream TLS verification. They do not
generate AWS service quota pressure or reproduce a regional AWS outage. Socket
timeouts may cause valid HTTP submission retries; assertions check one logical
token/query ID rather than requiring exactly one physical HTTP request.

Reported scanned bytes are not the billing quantity: billing can apply per-query
minimums, and S3 requests/storage and catalog usage are separate. Source-data
SSE-KMS allow/deny and AWS-owned managed results were tested; customer-KMS managed
result encryption, cross-account trust, SSO and web identity are outside this run.
The cloud CI target remains disabled until CI credentials and its workflow are
assigned; the local default identity is not copied to CI.

Real connection setup exposed an assumption in `test_connection()`: the service
listed `AwsDataCatalog` as `GLUE`, while `GetDataCatalog` returned not found.
Connection validation now reuses paginated catalog discovery for type/access
checks, then verifies database metadata and an actual SELECT. It does not add
catalog-name exceptions or suppress metadata errors. The existing connection-test
category caught this during live acceptance; offline regression covers discovery,
missing/unsupported catalogs and the actual-query requirement.

## Additional EXPLAIN validation: 2026-10-19

The standalone `test_explain_returns_query_plan` case ran twice against actual
Athena SQL engine v3 in `us-east-1` with managed results: one passed case per run
(3.84 and 3.97 seconds), zero skips, and `DataScannedInBytes=0` for each query.
The test uses `EXPLAIN SELECT 1 AS datus_explain_value` and verifies a successful,
nonempty plan containing the output alias. It requires only region/Workgroup
configuration, without a source database or prepared data.

The captured service response reported `StatementType=DML`, with `Query Plan`
as the first API row and `Fragment 0 [SINGLE]` as the first plan row. The connector
correctly removes the DML header and retains the plan. The unit regression now
uses this observed protocol rather than fabricating a headerless UTILITY EXPLAIN.

Each run created one uniquely tagged temporary Workgroup with managed results
and a 10 MiB scan cutoff, then disabled/deleted it and verified it was absent.
No S3 buckets, Glue databases, capacity reservations or servers were created.
These extra EXPLAIN checks are separate from the historical 28-case result-mode
counts. The optional writable table contract still awaits live validation.

See [AWS EXPLAIN behavior and scan considerations](https://docs.aws.amazon.com/athena/latest/ug/athena-explain-statement.html).
