# datus-hive

Hive database adapter for Datus.

## Installation

```bash
pip install datus-hive
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-sqlalchemy`
- `pyhive`
- `thrift`
- `thrift-sasl`
- `pure-sasl`

## Usage

The adapter is automatically registered with Datus when installed. Configure your Hive connection in your Datus configuration:

```yaml
agent:
  services:
    datasources:
      hive:
        type: hive
        host: 127.0.0.1
        port: 10000
        username: hive
        database: default
```

With authentication and session configuration:

```yaml
agent:
  services:
    datasources:
      hive_production:
        type: hive
        host: 127.0.0.1
        port: 10000
        database: mydb
        username: hive_user
        password: your_password
        auth: CUSTOM
        configuration:
          hive.execution.engine: spark
          spark.app.name: my_app
          spark.executor.memory: 1G
          spark.executor.instances: 2
```

Or use programmatically:

```python
from datus_hive import HiveConnector, HiveConfig

# Create connector
config = HiveConfig(
    host="127.0.0.1",
    port=10000,
    database="default",
    username="hive",
)

connector = HiveConnector(config)

# Test connection
connector.test_connection()

# Execute query
result = connector.execute(
    {"sql_query": "SELECT * FROM my_table LIMIT 10"},
    result_format="list",
)
print(result.sql_return)

# Get table list
tables = connector.get_tables()
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(table_name="my_table")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | str | `127.0.0.1` | HiveServer2 host |
| `port` | int | `10000` | HiveServer2 Thrift port |
| `server_type` | str | `hive` | Result protocol: `hive` or `inceptor` |
| `database` | str | `None` | Default database (falls back to `default`) |
| `username` | str | **required** | Hive username |
| `password` | str | `""` | Password (for LDAP/CUSTOM auth) |
| `auth` | str | `None` | Auth mechanism: `NONE`, `LDAP`, `CUSTOM`, `KERBEROS` |
| `kerberos_service_name` | str | `None` | Required for `KERBEROS`; service component of the server principal, usually `hive` |
| `configuration` | dict | `{}` | Hive session configuration key-value pairs |
| `timeout_seconds` | int | `30` | Connection timeout in seconds |

When `auth` is omitted, PyHive uses `NONE` (SASL PLAIN).
Authentication is not detected automatically. Set
`auth: LDAP` or `auth: CUSTOM` explicitly when supplying a password.

### Kerberos

Install the GSSAPI dependency, with system Kerberos libraries and development
headers available (for example, `libkrb5-dev` and a C compiler on Debian/Ubuntu):

```bash
pip install 'datus-hive[kerberos]'
```

Obtain a ticket before starting Datus. For example, with a keytab and a dedicated
credential cache:

```bash
export KRB5_CONFIG=/path/to/krb5.conf
export KRB5CCNAME=FILE:/path/to/datus.ccache
kinit -kt /path/to/alice.keytab alice@EXAMPLE.COM
```

```yaml
agent:
  services:
    datasources:
      secured_hive:
        type: hive
        host: hs2.example.com
        port: 10000
        database: default
        username: alice
        auth: KERBEROS
        kerberos_service_name: hive
```

This uses binary HiveServer2 Thrift with SASL GSSAPI, including Inceptor's
compatible endpoint. The example expects `hive/hs2.example.com@EXAMPLE.COM` on
the server. Use the hostname matching the service principal, with working DNS,
realm configuration, and synchronized clocks. The service name is the `hive`
component, not the complete principal or the client principal.

Authentication uses the principal in the current ticket cache; `username` does
not obtain a ticket or change that identity. Do not supply `password` for
Kerberos. Ticket acquisition and renewal remain the responsibility of the
application or deployment environment. The adapter does not run `kinit` or
change process-wide Kerberos settings. LDAP/CUSTOM continue to use passwords.

For integration tests, set `HIVE_AUTH=KERBEROS`,
`HIVE_KERBEROS_SERVICE_NAME=hive`, and the usual `HIVE_HOST`, `HIVE_PORT`, and
`HIVE_USERNAME` variables in a process with a valid ticket cache.

### Inceptor result types

Select Inceptor's result protocol explicitly for an Inceptor endpoint:

```yaml
agent:
  services:
    datasources:
      inceptor:
        type: hive
        server_type: inceptor
        host: inceptor.example.com
        port: 10000
        username: hive
        database: default
        auth: NONE
```

Keep the authentication settings appropriate to your endpoint. The default
`server_type: hive` retains Apache Hive's PyHive driver. Inceptor uses different
IDs for CHAR, VARCHAR and both interval types, and adds VARCHAR2, TIME, GEO,
JSON and CURSOR. Its connection uses a dedicated result decoder without
changing PyHive's process-wide type definitions or other connections.

VARCHAR2/CHAR/VARCHAR values retain their strings, including Unicode and padding.
TIME and intervals retain the server's textual representation (including
`24:00:00` and month intervals); JSON retains JSON text, and GEO retains the
server's text or binary payload. Nested CURSOR operation handles are fetched
as lists of records and closed after materialization, including on failure.
NULL values remain `None`. Unknown type IDs report the column name and ID.

This protocol targets the definitions in TDH CE 2024.5's
`inceptor-service-8.37.3.jar`. The local CE engine replaces non-ASCII strings
inside JSON values before returning them over HS2; converting that JSON to
STRING on the server has the same limitation. The driver preserves the value
actually returned by the server. Other server versions require their own
compatibility checks.

The scalar types and a native SYS_REFCURSOR OUT parameter are covered against
the local Inceptor engine. The cursor regression uses vendor prepared-call RPCs
to obtain a real handle, reads nested VARCHAR2 values with the result decoder,
and checks that the operation handle is closed afterwards. Protocol decoding
and cleanup failures also have unit tests.

The manual Inceptor fixture and readiness probe select this protocol. For the
generic integration suite against an Inceptor endpoint, additionally export
`HIVE_SERVER_TYPE=inceptor`. Run the result-type regression suite with:

```bash
INCEPTOR_TEST=1 python -m pytest tests/integration/inceptor/test_result_types.py -m integration -v
```

## Features

- Query execution with multiple result formats (list, csv, pandas, arrow)
- DDL execution (CREATE, ALTER, DROP)
- Metadata retrieval (databases, tables, views, schemas)
- DDL retrieval (SHOW CREATE TABLE)
- Sample data extraction
- Database context switching (USE statement)
- Connection pooling and management
- Hive session configuration support

Table listings (`get_tables` and `get_tables_with_ddl`) exclude ordinary views;
use `get_views` and `get_views_with_ddl` for views. Column metadata is read by
field name, preserving comments with both Apache Hive's standard `DESCRIBE`
result and Inceptor's additional default-value and constraint fields.
Table/view discovery requires `SHOW VIEWS` support and metadata-read permissions;
server errors are propagated to the caller. A successful empty view inventory is
returned as an empty list.

## Testing

### Unit Tests

```bash
uv run pytest datus-hive/tests/unit -v
```

### Integration Tests

Start Hive using Docker:

```bash
cd datus-hive
docker compose up -d

# Wait for Hive to be healthy (about 1-2 minutes)
docker inspect --format='{{.State.Health.Status}}' datus-hive-server
```

Run integration tests:

```bash
uv run pytest datus-hive/tests/integration -v
```

Stop Hive:

```bash
cd datus-hive
docker compose down
```

### TPC-H Test Data

Initialize TPC-H sample data for manual testing:

```bash
uv run python datus-hive/scripts/init_tpch_data.py

# With custom connection:
uv run python datus-hive/scripts/init_tpch_data.py --host localhost --port 10000 --username hive

# Clean re-init (drop existing tables first):
uv run python datus-hive/scripts/init_tpch_data.py --drop
```

This creates 5 TPC-H tables with sample data:

| Table | Rows |
|-------|------|
| `tpch_region` | 5 |
| `tpch_nation` | 25 |
| `tpch_customer` | 10 |
| `tpch_orders` | 15 |
| `tpch_supplier` | 5 |

## Requirements

- Python >= 3.10
- Apache Hive with `SHOW VIEWS` support for table/view discovery (tested with 4.0.1)
- datus-agent >= 0.3.0
- datus-sqlalchemy >= 0.1.0
- pyhive >= 0.7.0

## License

Apache License 2.0

### Manual Inceptor environment

[Local Inceptor setup](local/inceptor/README.md) provides an isolated Docker Compose
environment, idempotent initialization, authenticated storage/metadata readiness,
and strict manual query/metadata tests. It includes a local Kerberos KDC; LDAP and
CUSTOM tests accept a separately configured endpoint. The official TDH CE image
and a valid vendor evaluation/license are prerequisites. This target is not
registered in CI.
