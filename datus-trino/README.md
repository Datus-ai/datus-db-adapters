# datus-trino

Trino database adapter for Datus.

## Installation

```bash
pip install datus-trino
```

This will automatically install the required dependencies:
- `datus-db-core`
- `datus-sqlalchemy`
- `trino`

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
database:
  type: trino
  host: localhost
  port: 8080
  username: trino
  catalog: memory
  schema_name: default
```

Or use programmatically:

```python
from datus_trino import TrinoConnector, TrinoConfig

# Using config object
config = TrinoConfig(
    host="localhost",
    port=8080,
    username="trino",
    catalog="memory",
    schema_name="default",
)
connector = TrinoConnector(config)

# Or using dict
connector = TrinoConnector({
    "host": "localhost",
    "port": 8080,
    "username": "trino",
    "catalog": "memory",
    "schema_name": "default",
})

# Test connection
connector.test_connection()

# Execute query
result = connector.execute({"sql_query": 'SELECT * FROM "tpch"."tiny"."nation"'})
print(result.sql_return)

# Get catalogs
catalogs = connector.get_catalogs()
print(f"Catalogs: {catalogs}")

# Get table list
tables = connector.get_tables(catalog_name="tpch", schema_name="tiny")
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(catalog_name="tpch", schema_name="tiny", table_name="nation")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| host | str | "127.0.0.1" | Trino server host |
| port | int | 8080 | Trino server port |
| username | str | (required) | Username |
| password | str | "" | Password |
| catalog | str | "hive" | Default catalog |
| schema_name | str | "default" | Default schema |
| http_scheme | str | "http" | HTTP scheme (http/https) |
| verify | bool | True | Verify the HTTPS certificate chain and hostname |
| ca_cert | str or None | None | Readable PEM CA bundle file; HTTPS and verify=True required |
| timeout_seconds | int | 30 | Connection timeout |

## Verified HTTPS (0.1.4)

The private CA setting targets the upcoming `datus-trino>=0.1.4`, with
`datus-sqlalchemy>=0.1.10` and `datus-db-core>=0.1.8`. These versions are not yet
published; use the workspace source packages until release.

```yaml
type: trino
host: trino.example.com
port: 8443
username: analytics
catalog: hive
schema_name: analytics
http_scheme: https
verify: true
ca_cert: /absolute/path/trino-ca.pem
```

Omit `ca_cert` to use the Python client's Requests trust configuration. `verify`
stays a boolean. CA paths expand `~` and resolve relative to the working directory;
invalid PEM files, HTTP + CA, and disabled verification + CA are rejected.
This setting covers server verification; it does not accept inline PEM or client
certificates. Explicit `verify: false` disables both chain and hostname checks and
is intended only for temporary development. HTTPS does not fall back to HTTP, and
ports do not choose the protocol. Old profiles keep HTTP on port 8080 by default.

From the workspace root, run the isolated real TLS suite:

```bash
uv run --no-project python -m ci.run_tls_integration_tests trino
```

The runner generates disposable localhost certificates, uses random loopback ports,
checks readiness through the verified adapter, runs metadata/TPC-H/result contracts
and certificate rejection tests, and removes its Compose containers, volume and
network in `finally`. `TRINO_REQUIRED=1` makes connection failures fail the tests.
For a manually provisioned endpoint, fixtures and readiness share `TRINO_HOST`,
`TRINO_PORT`, `TRINO_USER`, `TRINO_PASSWORD`, `TRINO_CATALOG`, `TRINO_SCHEMA`,
`TRINO_HTTP_SCHEME`, `TRINO_VERIFY`, and `TRINO_CA_CERT`.

See [Trino TLS and HTTPS](https://trino.io/docs/current/security/tls.html) and the
[Python client](https://github.com/trinodb/trino-python-client#ssl).

## Features

- Query execution via Trino SQL
- DDL execution (CREATE, ALTER, DROP)
- Three-level hierarchy: catalog -> schema -> table
- Catalog switching (cross-catalog queries)
- Metadata retrieval (catalogs, schemas, tables, views, columns)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Connection pooling and management

## Testing

### Quick Start

```bash
cd datus-trino

# Unit tests (no database required)
uv run pytest tests/ -m "not integration" -v

# All tests with coverage
uv run pytest tests/ -v --cov=datus_trino --cov-report=term-missing
```

### Integration Tests (Requires Trino)

```bash
# Start Trino container (with TPC-H connector pre-configured)
docker compose up -d

# Wait for container to become healthy (~30s)
docker compose ps

# Run integration tests
uv run pytest tests/integration/ -v

# Run only TPC-H tests
uv run pytest tests/integration/test_tpch.py -v

# Run acceptance tests (core functionality)
uv run pytest tests/ -m acceptance -v

# Stop Trino
docker compose down
```

### TPC-H Test Data

Trino has a built-in TPC-H connector that generates benchmark data on-the-fly using deterministic algorithms. No data import is needed. The Docker setup includes `etc/catalog/tpch.properties` which enables this connector.

Available schemas (scale factors):

| Schema | Description |
|--------|-------------|
| `tiny` | ~10 rows per table (default for tests) |
| `sf1` | Scale factor 1 (~1.5M orders) |
| `sf100` | Scale factor 100 (~150M orders) |
| `sf1000` | Scale factor 1000 (~1.5B orders) |

Standard TPC-H tables (8 total):

`customer`, `lineitem`, `nation`, `orders`, `part`, `partsupp`, `region`, `supplier`

#### Datus Agent Configuration

To use TPC-H data in Datus agent:

```yaml
# In agent.yml under namespace
trino_tpch:
  type: trino
  host: 127.0.0.1
  port: 8080
  username: trino
  catalog: tpch
  schema_name: tiny
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires running Trino server |
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
black datus_trino tests
isort datus_trino tests
ruff check datus_trino tests
```

## Requirements

- Python >= 3.12
- Trino >= 400
- datus-db-core >= 0.1.8
- datus-sqlalchemy >= 0.1.10

## License

Apache License 2.0
