# datus-clickhouse

ClickHouse database adapter for Datus.

## Installation

```bash
pip install datus-clickhouse
```

This will automatically install the required dependencies:
- `datus-sqlalchemy`
- `clickhouse-sqlalchemy`

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
agent:
  services:
    datasources:
      clickhouse_prod:
        type: clickhouse
        host: clickhouse.example.com
        protocol: https
        port: 8443
        username: ${CLICKHOUSE_USER}
        password: ${CLICKHOUSE_PASSWORD}
        database: analytics
        verify: true
        # Optional for a private CA; omit for the default Requests trust configuration.
        ca_cert: /absolute/path/clickhouse-ca.pem
```

Or use programmatically:

```python
from datus_clickhouse import ClickHouseConfig, ClickHouseConnector

# Using config object
config = ClickHouseConfig(
    host="localhost",
    port=8123,
    username="default",
    password="your_password",
    database="mydb",
)
connector = ClickHouseConnector(config)

# Or using dict
connector = ClickHouseConnector({
    "host": "localhost",
    "port": 8123,
    "username": "default",
    "password": "your_password",
    "database": "mydb",
})

# Test connection
connector.test_connection()

# Execute query
result = connector.execute({"sql_query": "SELECT * FROM users LIMIT 10"})
print(result.sql_return)

# Get table list
tables = connector.get_tables()
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(table_name="users")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| host | str | "localhost" | ClickHouse server host |
| port | int | 8123 / 8443 | Default follows `protocol`; explicit custom ports are preserved |
| username | str | (required) | Username |
| password | str | "" | Password |
| database | str | None | Default database |
| timeout_seconds | int | 30 | SQLAlchemy pool wait timeout; does not impose a query deadline |
| protocol | str | `http` | `http` or `https`; the port alone does not enable TLS |
| verify | bool | `true` | Verify the HTTPS certificate chain and hostname |
| ca_cert | str | unset | PEM CA bundle file for verified HTTPS |

## HTTPS and certificates

TLS configuration is available in `datus-clickhouse>=0.1.4`, using `datus-sqlalchemy>=0.1.10` and `datus-db-core>=0.1.8`. The existing HTTP(S) driver and query/result contracts are retained.

Existing profiles without `protocol` keep HTTP and port 8123. Set `protocol: https` for TLS; its default port is 8443. Use the server's HTTP(S) endpoint, including a custom proxy port if needed; native TCP ports 9000/9440 use a different protocol.

HTTPS verifies the certificate chain and hostname by default. With no `ca_cert`, Requests uses its normal trust configuration, including `REQUESTS_CA_BUNDLE` when configured. For a private CA, supply a readable PEM bundle file. Paths expand `~`; relative paths resolve against the process working directory. Certificate files are read from the runtime filesystem; inline PEM and client-certificate authentication are outside this release.

`ca_cert` requires both `protocol: https` and `verify: true`. An invalid bundle or conflicting settings fail configuration validation. A wrong CA or hostname fails the TLS connection. TLS errors do not trigger a retry using HTTP or disabled verification. `verify: false` explicitly disables chain/hostname verification and is intended only for local diagnosis.

## Features

- Query execution via ClickHouse SQL (SELECT)
- DDL execution (CREATE, ALTER, DROP)
- DML operations (INSERT, ALTER TABLE UPDATE, DELETE)
- Metadata retrieval (databases, tables, views, columns)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Connection pooling and management
- Comprehensive error handling

## Testing

### Quick Start

```bash
cd datus-clickhouse

# Unit tests (no database required)
uv run pytest tests/unit -m "not integration" -v

# All tests with coverage
uv run pytest tests/ -v --cov=datus_clickhouse --cov-report=term-missing
```

### Integration Tests (Requires ClickHouse Server)

```bash
# Start ClickHouse container
docker compose up -d

# Wait for container to become healthy (~15s)
docker compose ps

# Run integration tests
uv run pytest tests/integration/ -v

# Run only TPC-H tests
uv run pytest tests/integration/test_tpch.py -v

# Run acceptance tests (core functionality)
uv run pytest tests/ -m acceptance -v

# Stop ClickHouse
docker compose down
```

### TPC-H Test Data

Integration tests include TPC-H benchmark data for realistic query testing. The `tpch_setup` fixture (session-scoped) automatically creates 5 tables with sample data:

| Table | Rows | Description |
|-------|------|-------------|
| `tpch_region` | 5 | Standard TPC-H regions |
| `tpch_nation` | 25 | Standard TPC-H nations |
| `tpch_customer` | 10 | Simplified customer data |
| `tpch_orders` | 15 | Simplified order data |
| `tpch_supplier` | 5 | Simplified supplier data |

Tables are created at the start of the test session and dropped after all tests complete.

#### Initialize TPC-H Data Manually

To create TPC-H data for use with Datus (outside of tests):

```bash
# Basic usage
uv run python scripts/init_tpch_data.py

# Drop existing tables and re-create
uv run python scripts/init_tpch_data.py --drop

# Custom connection
uv run python scripts/init_tpch_data.py --host 192.168.1.100 --port 8123 --username admin --password secret
```

### Verified HTTPS integration

From the workspace root, the existing TLS runner creates a uniquely named Compose project, short-lived CA/server certificates, and random loopback ports. It runs readiness and the full ClickHouse integration suite, then removes its containers, volumes, network, and certificates:

```bash
uv run --no-project python -m ci.run_tls_integration_tests clickhouse
```

The suite covers valid TLS, untrusted/wrong CA, hostname mismatch, explicit development verification override, protocol mismatch, reconnection, the setup CLI, and the existing metadata/contract/TPC-H cases. TLS failures after target selection fail rather than skip.

Connection settings for tests, readiness and the setup CLI share `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_PROTOCOL`, `CLICKHOUSE_VERIFY`, and `CLICKHOUSE_CA_CERT`. `CLICKHOUSE_REQUIRED=1` makes an unavailable optional local target fail. Container provisioning uses separate `DATUS_TEST_CLICKHOUSE_USER/PASSWORD/DATABASE` variables.

The setup CLI accepts matching options; explicit arguments override environment values before validation:

```bash
uv run --package datus-clickhouse python datus-clickhouse/scripts/init_tpch_data.py \
  --host localhost --port 18443 --protocol https --ca-cert /absolute/path/ca.crt --drop
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires running ClickHouse server |
| `acceptance` | Core functionality validation for CI/CD |

## Development

### Setup

```bash
# From workspace root
uv sync --all-packages

# Or install in editable mode
uv pip install -e .
```

### Code Quality

```bash
black datus_clickhouse tests
isort datus_clickhouse tests
ruff check datus_clickhouse tests
```

## ClickHouse SQL Notes

ClickHouse has some syntax differences from standard SQL:

- **UPDATE**: Use `ALTER TABLE <table> UPDATE ... WHERE ...` instead of `UPDATE <table> SET ...`
- **DELETE**: Supports lightweight deletes with `DELETE FROM <table> WHERE ...`
- **Identifiers**: Use backticks for quoting: `` `database`.`table` ``
- **No schema layer**: Databases serve as schemas; there is no separate schema concept

## Requirements

- Python >= 3.12
- ClickHouse >= 20.1
- datus-sqlalchemy >= 0.1.10
- datus-db-core >= 0.1.8
- clickhouse-sqlalchemy >= 0.3.2

## License

Apache License 2.0
