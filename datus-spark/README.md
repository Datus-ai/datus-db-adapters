# datus-spark

Spark SQL database adapter for Datus, connecting via HiveServer2/Thrift protocol.

## Installation

```bash
pip install datus-spark
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-sqlalchemy`
- `pyhive`
- `thrift`
- `thrift-sasl`
- `pure-sasl`

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
database:
  type: spark
  host: localhost
  port: 10000
  username: spark
  database: default
  auth_mechanism: NONE
```

Or use programmatically:

```python
from datus_spark import SparkConnector, SparkConfig

# Using config object
config = SparkConfig(
    host="localhost",
    port=10000,
    username="spark",
    password="",
    database="default",
    auth_mechanism="NONE",
)
connector = SparkConnector(config)

# Or using dict
connector = SparkConnector({
    "host": "localhost",
    "port": 10000,
    "username": "spark",
    "database": "default",
})

# Test connection
connector.test_connection()

# Execute query
result = connector.execute({"sql_query": "SELECT * FROM `default`.`my_table` LIMIT 10"})
print(result.sql_return)

# Get table list
tables = connector.get_tables(database_name="default")
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(database_name="default", table_name="my_table")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| host | str | "127.0.0.1" | Spark Thrift Server host |
| port | int | 10000 | Spark Thrift Server port |
| username | str | (required) | Username |
| password | str | "" | Password |
| database | str | None | Default database (falls back to `default`) |
| auth_mechanism | str | "NONE" | Authentication mechanism (NONE, NOSASL, LDAP, KERBEROS, CUSTOM) |
| timeout_seconds | int | 30 | Connection timeout |

`auth_mechanism` is passed directly to PyHive. Use `LDAP` or `CUSTOM` when providing a password. `PLAIN` is
the underlying SASL mechanism used by those modes, not a valid PyHive authentication mode.

## Features

- Query execution via Spark SQL (SELECT)
- DDL execution (CREATE, ALTER, DROP)
- Metadata retrieval (databases, tables, views, columns)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Connection pooling and management
- Context manager support

## Testing

### Quick Start

```bash
cd datus-spark

# Unit tests (no database required)
uv run pytest tests/ -m "not integration" -v

# All tests with coverage
uv run pytest tests/ -v --cov=datus_spark --cov-report=term-missing
```

### Integration Tests (Requires Spark Thrift Server)

```bash
# Start Spark Thrift Server container
docker compose up -d

# Wait for container to become healthy (~60s)
docker compose ps

# Run integration tests
uv run pytest tests/integration/ -v

# Run only TPC-H tests
uv run pytest tests/integration/test_tpch.py -v

# Run acceptance tests (core functionality)
uv run pytest tests/ -m acceptance -v

# Stop Spark
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
uv run python scripts/init_tpch_data.py --host 192.168.1.100 --port 10000
```

### Test Statistics

- **Unit Tests**: 46 tests (config validation, connector logic, identifiers)
- **Integration Tests**: 24 tests (connection, metadata, SQL execution, TPC-H)
- **Total**: 70 tests

### Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires running Spark Thrift Server |
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
black datus_spark tests
isort datus_spark tests
ruff check datus_spark tests
```

## Requirements

- Python >= 3.12
- Apache Spark >= 3.0 with Thrift Server enabled
- datus-agent > 0.2.1
- datus-sqlalchemy >= 0.1.0

## License

Apache License 2.0
