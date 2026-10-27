# Datus Snowflake Adapter

Snowflake database adapter for Datus Agent, providing native Snowflake connector support.

## Features

- **Native Snowflake SDK**: Uses `snowflake-connector-python` for optimal performance
- **Full Snowflake Support**: Databases, schemas, tables, views, and materialized views
- **Efficient Metadata Retrieval**: Uses SHOW commands for fast metadata queries
- **Arrow-based Execution**: High-performance query execution with Apache Arrow
- **Multiple Result Formats**: CSV, Pandas DataFrame, Arrow Table, and Python list
- **Complete CRUD Operations**: INSERT, UPDATE, DELETE, and DDL support

## Installation

```bash
pip install datus-snowflake
```

This will automatically install the required dependencies:
- `datus-db-core>=0.1.9`
- `snowflake-connector-python>=3.6.0,<5.0.0`

## Usage

### Basic Connection

```python
from datus_snowflake import SnowflakeConnector

# Create connector
connector = SnowflakeConnector(
    {
        "account": "myaccount",
        "username": "myuser",
        "password": "mypassword",
        "warehouse": "my_warehouse",
        "database": "my_database",
        "schema": "my_schema",
    }
)

# Test connection
result = connector.test_connection()
print(result)  # {'success': True, 'message': 'Connection successful', 'databases': ''}
```

### Key Pair Authentication (recommended for MFA accounts and CI)

Snowflake accounts with MFA enabled reject plain password logins from non-interactive
clients. Use RSA key pair authentication (`SNOWFLAKE_JWT`) instead — it bypasses MFA
and is Snowflake's recommended path for programmatic access. Register the public key
on your Snowflake user once (`ALTER USER <u> SET RSA_PUBLIC_KEY = '<base64>'`).

For hosted/SaaS deployments, store the private key as an encrypted secret field and
pass it as `private_key`; this avoids writing private keys to local files:

```python
from datus_snowflake import SnowflakeConnector

connector = SnowflakeConnector(
    {
        "account": "myaccount",
        "username": "myuser",
        "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
        # Only needed if the private key is encrypted:
        "private_key_file_pwd": "key-passphrase",
        "warehouse": "my_warehouse",
        "database": "my_database",
        "schema": "my_schema",
    }
)
```

For local or CI setups where a key file is already available, point the connector at
the matching private key file:

```python
from datus_snowflake import SnowflakeConnector

connector = SnowflakeConnector(
    {
        "account": "myaccount",
        "username": "myuser",
        "private_key_file": "/path/to/rsa_key.p8",
        # Only needed if the key file is encrypted:
        "private_key_file_pwd": "key-passphrase",
        "warehouse": "my_warehouse",
        "database": "my_database",
        "schema": "my_schema",
    }
)
```

`private_key` takes precedence over `private_key_file` and `password` when
provided. Without `private_key`, exactly one of `password` or `private_key_file`
must be provided; passing both (or neither) raises a `ValueError`.

### AWS Secrets Manager credentials

Install the optional AWS dependency with `pip install 'datus-snowflake[aws]>=0.1.10'`.
Authentication remains Snowflake key-pair or password authentication; Secrets Manager
supplies the credential at physical login time.

```python
connector = SnowflakeConnector({
    "account": "myaccount",
    "warehouse": "my_warehouse",
    "role": "READER",
    "auth_mode": "key_pair",
    "secret_ref": "arn:aws:secretsmanager:us-east-1:123456789012:secret:snowflake-example",
    "secret_key_mapping": {
        "username": "sf_user",
        "private_key": "sf_key",
        "private_key_file_pwd": "sf_passphrase",
    },
    "secret_cache_ttl_seconds": 300,
    "aws": {"region": "us-east-1", "profile": "datus-prod"},
})
```

The Secret's JSON maps top-level keys to nonempty strings. Omit the passphrase
mapping for an unencrypted PEM. Without mapping (or with all mapping fields blank),
the entire `SecretString` is the credential: specify a configured `username` and
use a raw unencrypted PEM or `auth_mode: password` with a raw password. JSON
passwords use a `password` mapping. `SecretBinary` is unsupported. Secret references
cannot be combined with inline credentials or key files. Other Secret fields cannot
override the configured account, role, warehouse or namespace. Mapped/configured
usernames must agree; changing the effective username requires a new connector and
refreshing identity-dependent metadata caches.

The cache lasts 1–300 seconds (default 300) and new logins read `AWSCURRENT` after
expiry. Existing sessions continue without polling or periodic disconnection.
`refresh_credentials()` explicitly refreshes the cache for the next login without
closing the current connection. `close(); connect()` creates a new session using
the current cache, restoring managed database/schema and configured role/warehouse.
Temporary objects and arbitrary session settings are not restored; business SQL is
never automatically replayed. An unexpectedly closed SDK session returns a
connection error; call `connect()` explicitly to start a fresh session before
submitting more SQL. Keep old credentials valid through the cache window
while publishing and validating their replacement. Early revocation can fail new
logins that still use cached credentials. AWS Secrets Manager changes are eventually
consistent: verify that the replacement is visible as `AWSCURRENT` before retiring
the old key; the cache TTL does not bound AWS propagation. Password authentication still follows the
Snowflake user's MFA policy.

AWS uses the SDK credential chain, including refreshable assume-role profiles. Set
`role_arn`, `source_profile` (or `credential_source`) and `external_id` in the shared
AWS config and select `aws.profile`; no static STS credentials are copied. The caller
and target trust policy must permit AssumeRole. Grant the reading identity
`secretsmanager:GetSecretValue` and, for a customer-managed key, `kms:Decrypt`.
`aws.ca_bundle` configures AWS HTTPS, not Snowflake TLS.

See the [Agent profile documentation](https://docs.datus.ai/configuration/datasources/snowflake/)
for the YAML configuration.

### Execute Queries

```python
# Execute query and get CSV result
result = connector.execute_query("SELECT * FROM users LIMIT 10")
print(result.sql_return)  # CSV string

# Execute query and get pandas DataFrame
result = connector.execute_query("SELECT * FROM users LIMIT 10", result_format="pandas")
df = result.sql_return
print(df.head())

# Execute query and get Arrow table
result = connector.execute_query("SELECT * FROM users LIMIT 10", result_format="arrow")
arrow_table = result.sql_return
print(arrow_table.schema)
```

### Metadata Operations

```python
# Get databases
databases = connector.get_databases()
print(f"Databases: {databases}")

# Get schemas
schemas = connector.get_schemas(database_name="my_database")
print(f"Schemas: {schemas}")

# Get tables
tables = connector.get_tables(database_name="my_database", schema_name="public")
print(f"Tables: {tables}")

# Get views
views = connector.get_views(database_name="my_database", schema_name="public")
print(f"Views: {views}")

# Get materialized views
mvs = connector.get_materialized_views(database_name="my_database", schema_name="public")
print(f"Materialized Views: {mvs}")
```

### Get Table Schema

```python
# Get table structure
schema = connector.get_schema(
    database_name="my_database",
    schema_name="public",
    table_name="users"
)

for column in schema[:-1]:  # Last item is table metadata
    print(f"{column['name']}: {column['type']} (nullable: {column['nullable']})")
```

### Get DDL Definitions

```python
# Get tables with DDL
tables_with_ddl = connector.get_tables_with_ddl(
    database_name="my_database",
    schema_name="public"
)

for table in tables_with_ddl:
    print(f"\nTable: {table['table_name']}")
    print(f"DDL:\n{table['definition']}")

# Get views with DDL
views_with_ddl = connector.get_views_with_ddl(
    database_name="my_database",
    schema_name="public"
)

# Get materialized views with DDL
mvs_with_ddl = connector.get_materialized_views_with_ddl(
    database_name="my_database",
    schema_name="public"
)
```

### Get Sample Data

```python
# Get sample rows from specific tables
samples = connector.get_sample_rows(
    tables=["users", "orders"],
    top_n=5,
    database_name="my_database",
    schema_name="public"
)

for sample in samples:
    print(f"\nTable: {sample['table_name']}")
    print(sample['sample_rows'])  # CSV format
```

### CRUD Operations

```python
# INSERT
result = connector.execute_insert(
    "INSERT INTO users (name, email) VALUES ('John', 'john@example.com')"
)
print(f"Inserted rows: {result.row_count}")

# UPDATE
result = connector.execute_update(
    "UPDATE users SET email = 'newemail@example.com' WHERE name = 'John'"
)
print(f"Updated rows: {result.row_count}")

# DELETE
result = connector.execute_delete(
    "DELETE FROM users WHERE name = 'John'"
)
print(f"Deleted rows: {result.row_count}")

# DDL
result = connector.execute_ddl(
    "CREATE TABLE test_table (id INT, name VARCHAR(100))"
)
print(f"DDL executed: {result.success}")
```

### Context Switching

```python
# Switch database
connector.do_switch_context(database_name="another_database")

# Switch schema
connector.do_switch_context(
    database_name="my_database",
    schema_name="another_schema"
)
```

## Configuration with Datus Agent

When using with Datus Agent, the adapter is automatically discovered via entry points:

```yaml
# config.yaml — password authentication
database:
  type: snowflake
  account: myaccount
  username: myuser
  password: mypassword
  warehouse: my_warehouse
  database: my_database
  schema: my_schema
```

```yaml
# config.yaml — key pair authentication with an in-memory/DB-backed PEM secret
database:
  type: snowflake
  account: myaccount
  username: myuser
  private_key: |
    -----BEGIN PRIVATE KEY-----
    ...
    -----END PRIVATE KEY-----
  # private_key_file_pwd: key-passphrase   # only for encrypted keys
  warehouse: my_warehouse
  database: my_database
  schema: my_schema
```

```yaml
# config.yaml — key pair authentication with a local file
database:
  type: snowflake
  account: myaccount
  username: myuser
  private_key_file: /path/to/rsa_key.p8
  # private_key_file_pwd: key-passphrase   # only for encrypted keys
  warehouse: my_warehouse
  database: my_database
  schema: my_schema
```

The adapter will be automatically loaded when you use `type: snowflake`.

## Architecture

This adapter:
- Inherits from `BaseSqlConnector` in `datus-db-core`
- Uses native Snowflake connector for optimal performance
- Implements all required abstract methods
- Provides Snowflake-specific optimizations (SHOW commands, Arrow format)

## Development

```bash
# Install in development mode
cd datus-snowflake
pip install -e .

# Run unit tests without a database
uv run python -m pytest tests/unit -m 'not integration'

# Run live tests after exporting the SNOWFLAKE_* configuration
uv run python -m pytest tests/integration -m integration
```

Live tests use `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_WAREHOUSE`,
`SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, optional `SNOWFLAKE_ROLE`, and either
`SNOWFLAKE_PASSWORD` or `SNOWFLAKE_PRIVATE_KEY_FILE` with optional
`SNOWFLAKE_PRIVATE_KEY_FILE_PWD`.

The dedicated Secret acceptance module is opt-in:

```bash
DATUS_SECRET_AWS_TESTS=1 DATUS_SNOWFLAKE_SECRET_TESTS=1 \
  uv run python -m pytest tests/integration/test_secret_rotation.py -m integration
```

AWS uses `DATUS_SECRET_AWS_REGION` (default `us-east-1`), optional
`DATUS_SECRET_AWS_PROFILE`, and the SDK credential chain. It creates and deletes
ownership-tagged temporary Secrets. Omit `DATUS_SNOWFLAKE_SECRET_TESTS` to run
AWS-only checks. Full rotation additionally requires the existing Snowflake key-pair
login to manage users, roles and warehouse grants. It creates a temporary service
user/role, rotates its two keys, checks old-key rejection and session continuity,
then verifies cleanup. It never changes the login user's keys. CI is explicitly
disabled for this cloud target until scoped credentials and a workflow are assigned.

For a single-account simulation of the third-party role flow:

```bash
DATUS_SECRET_ROLE_TESTS=1 DATUS_SNOWFLAKE_SECRET_TESTS=1 \
  uv run python -m pytest tests/integration/test_secret_assume_role.py -m integration
```

The provisioning identity additionally needs permission to create/tag/delete IAM
roles and manage their inline policies. This module creates two owned temporary
roles and two Secrets, and writes isolated temporary profiles referencing the
existing AWS credentials. It checks profile-based role chaining, correct/wrong/
missing External IDs, untrusted callers, source-role and Secret resource isolation,
and a real Snowflake key-pair login. Omit `DATUS_SNOWFLAKE_SECRET_TESTS` for AWS-only
acceptance. Existing AWS configuration and credentials are left unchanged. Cleanup
verifies role absence and records accepted asynchronous Secret deletion. Both roles belong to one AWS
account: a true second-account trust/organization-policy/KMS boundary still requires
separate acceptance.

Customer-managed KMS acceptance can also run in the existing AWS account:

```bash
DATUS_SECRET_KMS_TESTS=1 DATUS_SNOWFLAKE_SECRET_TESTS=1 \
  uv run python -m pytest tests/integration/test_secret_kms.py -m integration \
  --junitxml=/tmp/snowflake-secret-kms.xml
```

The provisioning identity needs KMS CreateKey/GetKeyPolicy/PutKeyPolicy/DescribeKey,
TagResource/ListResourceTags, GenerateDataKey/Decrypt, DisableKey/EnableKey and
ScheduleKeyDeletion, plus the temporary IAM/Secret provisioning permissions above.
The suite creates one owned symmetric KMS key, a KMS-encrypted Secret, a control
Secret and four restricted reader roles. It verifies successful reads/login,
missing Secret or KMS permissions, an explicit key-policy denial, and disabling/
re-enabling the key. The control Secret proves that KMS-denied roles still have
effective Secret read permission. All denial probes bypass adapter credential
caching; they do not require existing Snowflake sessions to terminate.

Cleanup deletes the owned Secrets and roles and verifies that the KMS key enters
`PendingDeletion` with a seven-day waiting period. The key is not immediately
deleted. JUnit suite properties record its ID, state and scheduled deletion date
for a subsequent live audit. Omit `DATUS_SNOWFLAKE_SECRET_TESTS` for AWS-only checks.

Cleanup permits unrelated account tags while requiring every ownership tag to match.
Secret deletion is asynchronous: the JUnit `aws_secret_cleanup` properties distinguish
`deleted` from `deletion_requested` and include the accepted deletion timestamp.
API failures still fail the suite; physical removal is checked separately in the live audit.

## License

Apache License 2.0
