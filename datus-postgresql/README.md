# datus-postgresql

PostgreSQL database adapter for [Datus](https://github.com/Datus-ai/datus-agent).

## Amazon Aurora PostgreSQL

Aurora uses this adapter with the PostgreSQL dialect. Set `aurora=True` and provide
`sslrootcert`; the adapter requires `sslmode=verify-full`. Password authentication
needs no AWS SDK. Install `pip install 'datus-postgresql[aws]'` for IAM or Secrets Manager.

```python
import os
from datus_postgresql import PostgreSQLConnector

connector = PostgreSQLConnector({
    "host": os.environ["AURORA_POSTGRESQL_ENDPOINT"],
    "username": os.environ["AURORA_DB_USER"],
    "database": os.environ["AURORA_DATABASE"],
    "aurora": True,
    "auth_mode": "iam",
    "aws": {"region": os.environ["AWS_REGION"]},
    "sslrootcert": os.environ["AURORA_CA_BUNDLE"],
})
try:
    result = connector.execute_query("SELECT 1", result_format="list")
    assert result.success, result.error
finally:
    connector.close()
```

TLS and dynamic credentials apply to the base engine and every cached database
engine. See [Aurora authentication and lifecycle](../docs/aurora.md) for the full
contract, IAM permissions/user setup, Role profiles, Secret rotation and pending
cloud acceptance. From the workspace root,
`uv run --no-project python -m ci.run_tls_integration_tests postgresql` runs verified
TLS, multiple databases, shared contracts and wrong-CA/hostname rejection.

## Overview

This adapter provides PostgreSQL connectivity for Datus, supporting full SQL operations, metadata discovery, schema management, and materialized views via the standard PostgreSQL protocol.

## Installation

```bash
pip install datus-postgresql
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-sqlalchemy` (which includes SQLAlchemy and psycopg2)

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection:

```yaml
namespace:
  postgresql_prod:
    type: postgresql
    host: localhost
    port: 5432
    username: postgres
    password: your_password
    database: mydb
    schema: public
```

Or use programmatically:

```python
from datus_postgresql import PostgreSQLConnector, PostgreSQLConfig

# Using config object
config = PostgreSQLConfig(
    host="localhost",
    port=5432,
    username="postgres",
    password="password",
    database="mydb",
    schema_name="public",
)

connector = PostgreSQLConnector(config)

# Or using dict
connector = PostgreSQLConnector({
    "host": "localhost",
    "port": 5432,
    "username": "postgres",
    "password": "password",
    "database": "mydb",
})

# Use context manager for automatic cleanup
with connector:
    # Test connection
    connector.test_connection()

    # Execute queries
    result = connector.execute({"sql_query": "SELECT * FROM users LIMIT 10"})
    print(result.sql_return)

    # Get tables
    tables = connector.get_tables(schema_name="public")
    print(f"Tables: {tables}")

    # Get table schema
    columns = connector.get_schema(schema_name="public", table_name="users")
    for col in columns:
        print(f"  {col['name']}: {col['type']}")

    # Get materialized views
    mvs = connector.get_materialized_views(schema_name="public")
    print(f"Materialized Views: {mvs}")
```

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `host` | str | `"127.0.0.1"` | PostgreSQL server host |
| `port` | int | `5432` | PostgreSQL server port |
| `username` | str | required | PostgreSQL username |
| `password` | str | `""` | PostgreSQL password |
| `database` | str | `None` | Default database name |
| `schema_name` | str | `"public"` | Default schema name |
| `sslmode` | str | `"prefer"` | SSL mode (`disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`) |
| `timeout_seconds` | int | `30` | Connection timeout in seconds |

## Features

- **Full SQL Support**: Execute queries, DDL, DML operations
- **Metadata Discovery**: Automatic discovery of databases, schemas, tables, views, and materialized views
- **DDL Generation**: Reconstruct CREATE TABLE / VIEW / MATERIALIZED VIEW statements
- **Schema Management**: Switch between schemas seamlessly
- **Sample Data**: Extract sample rows for data profiling
- **Connection Management**: SQLAlchemy-based connection pooling with SSL support
- **Multiple Result Formats**: pandas, arrow, csv, list

## Code Structure

```text
datus-postgresql/
├── datus_postgresql/
│   ├── __init__.py          # Package exports
│   ├── config.py            # PostgreSQLConfig (Pydantic model)
│   └── connector.py         # PostgreSQLConnector implementation
├── tests/
│   ├── unit/                # Unit tests with mocks (no database needed)
│   └── integration/         # Integration tests (requires PostgreSQL)
│       ├── conftest.py      # Test fixtures and TPC-H data setup
│       ├── test_integration.py  # General integration tests
│       └── test_tpch.py     # TPC-H benchmark data tests
├── scripts/
│   └── init_tpch_data.py    # TPC-H data initialization script
├── docker-compose.yml       # PostgreSQL test container
├── pyproject.toml
└── README.md
```

## Testing

### Quick Start

```bash
# 1. Start PostgreSQL test container
docker-compose up -d

# 2. Run tests
pytest tests/unit/ -v              # Unit tests (no database needed)
pytest tests/integration/ -v       # Integration tests (requires PostgreSQL)
pytest tests/ -v                   # All tests
```

### Test Types

- **Unit tests**: Configuration and connector logic with mocks (no database needed)
- **Integration tests**: Real database operations (SQL, metadata, schema)
- **TPC-H tests**: Analytical query tests using TPC-H benchmark data

### TPC-H Integration Tests

The adapter includes TPC-H benchmark data tests for validating analytical query capabilities.

#### TPC-H Tables

| Table | Rows | Description |
|-------|------|-------------|
| `tpch_region` | 5 | World regions |
| `tpch_nation` | 25 | Countries with region references |
| `tpch_supplier` | 5 | Suppliers with nation references |
| `tpch_customer` | 10 | Customers with nation references |
| `tpch_orders` | 15 | Orders with customer references |

#### Running TPC-H Tests

```bash
# Start PostgreSQL container
docker-compose up -d

# Run TPC-H integration tests
pytest tests/integration/test_tpch.py -v

# Initialize TPC-H data manually (for ad-hoc testing)
python scripts/init_tpch_data.py \
    --host localhost --port 5432 \
    --username test_user --password test_password \
    --database test --schema public

# Drop and recreate TPC-H tables
python scripts/init_tpch_data.py --drop
```

#### Environment Variables

Tests use these default values (matching docker-compose.yml):

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRESQL_HOST` | `localhost` | PostgreSQL server host |
| `POSTGRESQL_PORT` | `5432` | PostgreSQL server port |
| `POSTGRESQL_USER` | `test_user` | PostgreSQL username |
| `POSTGRESQL_PASSWORD` | `test_password` | PostgreSQL password |
| `POSTGRESQL_DATABASE` | `test` | Database name |
| `POSTGRESQL_SCHEMA` | `public` | Schema name |

## Requirements

- Python >= 3.12
- PostgreSQL >= 12
- datus-agent >= 0.2.1
- datus-sqlalchemy >= 0.1.0

## License

Apache License 2.0

## Related Packages

- `datus-sqlalchemy` - SQLAlchemy base connector
- `datus-mysql` - MySQL adapter
- `datus-redshift` - Amazon Redshift adapter
- `datus-snowflake` - Snowflake adapter
