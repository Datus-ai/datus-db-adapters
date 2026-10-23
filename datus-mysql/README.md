# datus-mysql

MySQL database adapter for Datus.

## Timeouts

`timeout_seconds` (default `30`, positive seconds) bounds pool checkout and is
also the default for PyMySQL connect, read, and write timeouts. Override these
independently with `connect_timeout_seconds`, `read_timeout_seconds`, and
`write_timeout_seconds`. `None` inherits the finite default. Long queries may
need a larger read timeout. Optional `query_timeout_seconds` configures the
server's SELECT deadline on every physical connection; it defaults to `None`
(preserve server settings). Socket inactivity timeouts are not total query
deadlines. See [timeout semantics and adapter audit](../docs/adapter-timeouts.md).

## Amazon Aurora MySQL

Aurora uses this adapter with the MySQL dialect. Set `aurora=True` and provide
`ssl_ca` to require certificate and hostname verification. Password authentication
needs no AWS SDK. Install `pip install 'datus-mysql[aws]'` for IAM or Secrets Manager.

```python
import os
from datus_mysql import MySQLConnector

connector = MySQLConnector({
    "host": os.environ["AURORA_MYSQL_ENDPOINT"],
    "username": os.environ["AURORA_DB_USER"],
    "database": os.environ["AURORA_DATABASE"],
    "aurora": True,
    "auth_mode": "iam",
    "aws": {"region": os.environ["AWS_REGION"]},
    "ssl_ca": os.environ["AURORA_CA_BUNDLE"],
})
try:
    result = connector.execute_query("SELECT 1", result_format="list")
    assert result.success, result.error
finally:
    connector.close()
```

See [Aurora authentication and lifecycle](../docs/aurora.md) for the full config
contract, IAM permissions/user setup, Role profiles, Secret rotation, writer/reader
endpoints and pending real-cloud acceptance. From the workspace root,
`uv run --no-project python -m ci.run_tls_integration_tests mysql` runs the isolated
TLS container regression, including wrong-CA/hostname rejection.

## Installation

```bash
pip install datus-mysql
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-sqlalchemy`
- `pymysql`

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
database:
  type: mysql
  host: localhost
  port: 3306
  username: root
  password: your_password
  database: your_database
```

Or use programmatically:

```python
from datus_mysql import MySQLConnector, MySQLConfig

# Using config object
config = MySQLConfig(
    host="localhost",
    port=3306,
    username="root",
    password="your_password",
    database="mydb"
)
connector = MySQLConnector(config)

# Or using dict
connector = MySQLConnector({
    "host": "localhost",
    "port": 3306,
    "username": "root",
    "password": "your_password",
    "database": "mydb"
})

# Test connection
connector.test_connection()

# Execute query
result = connector.execute({"sql_query": "SELECT * FROM users LIMIT 10"})
print(result.sql_return)

# Get table list
tables = connector.get_tables(database_name="mydb")
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(database_name="mydb", table_name="users")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Features

- Full CRUD operations (SELECT, INSERT, UPDATE, DELETE)
- DDL execution (CREATE, ALTER, DROP)
- Metadata retrieval (tables, views, schemas)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Connection pooling and management
- Comprehensive error handling

## Testing

### Quick Start

```bash
# Unit tests (no database required)
cd datus-mysql
uv run pytest tests/unit/ -v

# All tests with coverage
uv run pytest tests/ -v --cov=datus_mysql --cov-report=term-missing
```

### Integration Tests (Requires MySQL)

```bash
# Start MySQL container
cd datus-mysql
docker compose up -d

# Run integration tests
uv run pytest tests/integration/ -m integration -v

# Run TPC-H tests only
uv run pytest tests/integration/test_tpch.py -m integration -v

# Run all acceptance tests (unit + integration)
uv run pytest tests/ -m acceptance -v

# Stop MySQL
docker compose down
```

### TPC-H Test Data

The integration tests include TPC-H benchmark data for comprehensive testing:

| Table | Rows | Description |
|-------|------|-------------|
| `tpch_region` | 5 | Standard TPC-H regions |
| `tpch_nation` | 25 | Standard TPC-H nations |
| `tpch_customer` | 10 | Simplified customer data |
| `tpch_orders` | 15 | Simplified order data |
| `tpch_supplier` | 5 | Simplified supplier data |

The `tpch_setup` fixture (session-scoped) automatically creates tables, inserts data, and cleans up after tests complete.

### Initialize TPC-H Data Manually

You can also initialize TPC-H data manually using the provided script:

```bash
cd datus-mysql

# Using defaults (from docker-compose.yml)
uv run python scripts/init_tpch_data.py

# With custom connection
uv run python scripts/init_tpch_data.py --host localhost --port 3306 --username test_user --password test_password

# Drop existing tables first (clean re-init)
uv run python scripts/init_tpch_data.py --drop
```

### Test Statistics

- **Unit Tests**: 50 tests (config, connector, identifiers)
- **Integration Tests**: 31 tests (20 functional + 11 TPC-H)
- **Acceptance Tests**: 23 marked tests (subset of unit + integration)
- **Total**: 81 tests

### Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires a running MySQL instance |
| `acceptance` | Core functionality tests (subset of unit + integration) |

## Code Structure

```text
datus-mysql/
├── datus_mysql/
│   ├── __init__.py          # Package exports
│   ├── config.py            # MySQLConfig model
│   └── connector.py         # MySQLConnector implementation
├── tests/
│   ├── unit/
│   │   └── ...              # Unit tests (no database required)
│   └── integration/
│       ├── conftest.py      # Fixtures (config, connector, tpch_setup)
│       ├── test_integration.py  # Core integration tests
│       └── test_tpch.py     # TPC-H benchmark tests
├── scripts/
│   └── init_tpch_data.py    # Manual TPC-H data initialization
├── docker-compose.yml       # MySQL 8.0 test container
├── pyproject.toml
└── README.md
```

## Development

### Setup

```bash
# Install dependencies
uv sync

# Install in editable mode
uv pip install -e .
```

### Running Tests

```bash
# Fast unit tests
uv run pytest tests/unit/ -v

# With coverage
uv run pytest tests/ --cov=datus_mysql --cov-report=html
open htmlcov/index.html
```

### Code Quality

```bash
# Format code
black datus_mysql tests
isort datus_mysql tests

# Lint
ruff check datus_mysql tests
flake8 datus_mysql tests
```

## Requirements

- Python >= 3.12
- MySQL >= 5.7 or MariaDB >= 10.2
- datus-agent >= 0.3.0
- datus-sqlalchemy >= 0.1.0
- pymysql >= 1.0.0

## License

Apache License 2.0
