# datus-starrocks

StarRocks database adapter for Datus.

## Overview

StarRocks is a high-performance analytical database that uses the MySQL protocol. This adapter extends the MySQL connector with StarRocks-specific features:

- Multi-catalog support
- Materialized views
- StarRocks-specific metadata queries

## Installation

```bash
pip install datus-starrocks
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-mysql` (which includes `datus-sqlalchemy`)

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection:

```yaml
database:
  type: starrocks
  host: localhost
  port: 9030
  username: root
  password: your_password
  catalog: default_catalog
  database: your_database
```

Or use programmatically:

```python
from datus_starrocks import StarRocksConnector

# Create connector
connector = StarRocksConnector(
    host="localhost",
    port=9030,
    user="root",
    password="your_password",
    catalog="default_catalog",
    database="mydb"
)

# Use context manager for automatic cleanup
with connector:
    # Test connection
    connector.test_connection()

    # Get catalogs
    catalogs = connector.get_catalogs()
    print(f"Catalogs: {catalogs}")

    # Get databases in catalog
    databases = connector.get_databases(catalog_name="default_catalog")
    print(f"Databases: {databases}")

    # Get tables
    tables = connector.get_tables(catalog_name="default_catalog", database_name="mydb")
    print(f"Tables: {tables}")

    # Get materialized views
    mvs = connector.get_materialized_views(database_name="mydb")
    print(f"Materialized Views: {mvs}")

    # Get materialized views with DDL
    mvs_with_ddl = connector.get_materialized_views_with_ddl(database_name="mydb")
    for mv in mvs_with_ddl:
        print(f"\n{mv['table_name']}:")
        print(mv['definition'])

    # Execute query
    result = connector.execute_query("SELECT * FROM users LIMIT 10")
    print(result.sql_return)
```

## Features

### StarRocks-Specific Features
- **Multi-catalog support**: Query across multiple catalogs
- **Materialized views**: Full support for StarRocks materialized views
- **Catalog management**: Switch between catalogs seamlessly

### Inherited from MySQL
- Full CRUD operations (SELECT, INSERT, UPDATE, DELETE)
- DDL execution (CREATE, ALTER, DROP)
- Metadata retrieval (tables, views, schemas)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Connection pooling and management

## StarRocks-Specific Examples

### Working with Catalogs

```python
# List all catalogs
catalogs = connector.get_catalogs()

# Switch catalog
connector.switch_context(catalog_name="hive_catalog")

# Query with explicit catalog
tables = connector.get_tables(
    catalog_name="hive_catalog",
    database_name="my_hive_db"
)
```

### Materialized Views

```python
# Get materialized views
mvs = connector.get_materialized_views(database_name="mydb")

# Get materialized views with full DDL
mvs_with_ddl = connector.get_materialized_views_with_ddl(database_name="mydb")

for mv in mvs_with_ddl:
    print(f"Name: {mv['table_name']}")
    print(f"Database: {mv['database_name']}")
    print(f"Catalog: {mv['catalog_name']}")
    print(f"Definition: {mv['definition']}")
```

### Fully-Qualified Names

StarRocks supports three-part names: `catalog.database.table`

```python
# Build full name
full_name = connector.full_name(
    catalog_name="default_catalog",
    database_name="mydb",
    table_name="users"
)
# Result: `default_catalog`.`mydb`.`users`

# Query with full name
result = connector.execute_query(f"SELECT * FROM {full_name} LIMIT 10")
```

## Requirements

- Python >= 3.12
- StarRocks >= 2.0
- datus-agent >= 0.2.1
- datus-mysql >= 0.1.0

## Testing

### Quick Start

```bash
# 1. Start StarRocks test container
docker compose up -d
uv run python scripts/wait_for_starrocks.py

# 2. Run tests
./scripts/test.sh unit         # Unit tests (60 tests, ~0.03s)
./scripts/test.sh integration  # Integration tests (35 tests, ~1.5s)
./scripts/test.sh acceptance   # Acceptance tests (28 tests, CI subset)
./scripts/test.sh all          # All tests
```

### TPC-H Integration Tests

TPC-H integration tests use a simplified TPC-H dataset (5 tables: region, nation, customer, orders, supplier) to validate end-to-end query execution, JOIN operations, aggregations, and multi-format output.

```bash
# Start StarRocks and create test database
docker compose up -d
uv run python scripts/wait_for_starrocks.py

# Initialize TPC-H test data
uv run python scripts/init_tpch_data.py

# Run TPC-H integration tests
uv run pytest tests/integration/test_tpch.py -m integration -v

# Clean re-init (drop and recreate tables)
uv run python scripts/init_tpch_data.py --drop
```

**TPC-H Tables:**

| Table | Rows | Description |
|-------|------|-------------|
| `tpch_region` | 5 | Standard TPC-H regions |
| `tpch_nation` | 25 | Standard TPC-H nations |
| `tpch_customer` | 10 | Simplified customer data |
| `tpch_orders` | 15 | Simplified order data |
| `tpch_supplier` | 5 | Simplified supplier data |

Tables use `ENGINE=OLAP` with `PRIMARY KEY` and `DISTRIBUTED BY HASH` for StarRocks-optimized storage.

### Test Types

- **Unit tests** (60): Configuration and connector logic with Mocks (no database needed)
- **Integration tests** (35+): Real database operations (catalog, materialized views, SQL)
- **TPC-H tests** (11): Metadata, queries, JOINs, aggregations, multi-format output
- **Acceptance tests** (28): Critical functionality subset for CI/CD

### Environment Variables

Tests use these default values (automatically set by `./scripts/test.sh`):

| Variable | Default | Description |
|----------|---------|-------------|
| `STARROCKS_HOST` | `localhost` | StarRocks host |
| `STARROCKS_PORT` | `9030` | MySQL protocol port |
| `STARROCKS_USER` | `root` | Username |
| `STARROCKS_PASSWORD` | (empty) | Password |
| `STARROCKS_CATALOG` | `default_catalog` | Catalog |
| `STARROCKS_DATABASE` | `test` | Database |

## Code Structure

```text
datus-starrocks/
├── datus_starrocks/
│   ├── __init__.py          # Package exports
│   ├── config.py            # StarRocksConfig model
│   └── connector.py         # StarRocksConnector (extends MySQLConnector)
├── tests/
│   ├── unit/                # Unit tests (no database required)
│   └── integration/
│       ├── conftest.py      # Fixtures (config, connector, tpch_setup)
│       ├── test_connector.py # Core integration tests
│       └── test_tpch.py     # TPC-H benchmark tests
├── scripts/
│   ├── test.sh              # Test runner script
│   └── init_tpch_data.py    # Manual TPC-H data initialization
├── docker-compose.yml       # StarRocks 3.3-latest test container
├── pyproject.toml
└── README.md
```

## Connection Cleanup

The connector includes special handling for PyMySQL cleanup errors that can occur with StarRocks connections. Use the context manager pattern for automatic cleanup:

```python
with StarRocksConnector(...) as connector:
    # Your code here
    pass
# Connection automatically cleaned up
```

## License

Apache License 2.0

## Related Packages

- `datus-mysql` - MySQL adapter (base for StarRocks)
- `datus-sqlalchemy` - SQLAlchemy base connector
- `datus-snowflake` - Snowflake adapter
