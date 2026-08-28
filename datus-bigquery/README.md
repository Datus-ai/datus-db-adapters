# datus-bigquery

BigQuery database adapter for Datus.

## Installation

```bash
pip install datus-bigquery
```

This will automatically install the required dependencies:
- `datus-db-core`
- `datus-sqlalchemy`
- `sqlalchemy-bigquery`

## Configuration

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
namespace:
  analytics:
    type: bigquery
    project: your-gcp-project-id
    dataset: your_dataset
    credentials_path: /path/to/service-account.json
    # Or credentials_info / credentials_base64 for secret-managed deployments.
    # Configure only one credentials mechanism.
    billing_project_id: optional-quota-project
    location: US
```

Application Default Credentials are used when none of `credentials_path`,
`credentials_info`, or `credentials_base64` is configured. BigQuery's project
and dataset map to Datus's catalog and database levels; there is no schema
level below the dataset.

## Usage

Or use programmatically:

```python
from datus_bigquery import BigQueryConnector, BigQueryConfig

# Using config object
config = BigQueryConfig(
    project="your-gcp-project-id",
    dataset="your_dataset",
    credentials_path="/path/to/service-account.json",
    location="US"
)
connector = BigQueryConnector(config)

# Or using dict
connector = BigQueryConnector({
    "project": "your-gcp-project-id",
    "dataset": "your_dataset",
})

# Test connection
connector.test_connection()

# Execute query
result = connector.execute(
    {"sql_query": "SELECT * FROM `your-gcp-project-id.your_dataset.your_table` LIMIT 10"}
)
print(result.sql_return)

# Get table list
tables = connector.get_tables(database_name="your_dataset")
print(f"Tables: {tables}")

# Get table schema
schema = connector.get_schema(database_name="your_dataset", table_name="your_table")
for column in schema:
    print(f"{column['name']}: {column['type']}")
```

## Features

- Full query operations (SELECT, INSERT, DDL)
- Separate metadata retrieval for tables, views, and materialized views
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Project and dataset level navigation
- BigQuery-specific migration hints and DDL validation
- Packaged SQL generation skill for GoogleSQL
- Comprehensive error handling

## Testing

### Quick Start

```bash
# Unit tests (no BigQuery connection required)
cd datus-bigquery
uv run pytest tests/unit/ -v

# All tests with coverage
uv run pytest tests/ -v --cov=datus_bigquery --cov-report=term-missing
```

### Integration Tests (Requires BigQuery)

```bash
# Set environment variables
export BIGQUERY_PROJECT=your-gcp-project-id
export BIGQUERY_DATASET=datus_test
export BIGQUERY_CREDENTIALS_PATH=/path/to/service-account.json
# CI uses BIGQUERY_CREDENTIALS_INFO containing the service-account JSON object.
export BIGQUERY_LOCATION=US

# Run integration tests
cd datus-bigquery
uv run pytest tests/integration/ -m integration -v

# Run all acceptance tests (unit + integration)
uv run pytest tests/ -m acceptance -v
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `integration` | Requires a BigQuery connection |
| `acceptance` | Core functionality tests (subset of unit + integration) |

## Code Structure

```text
datus-bigquery/
├── datus_bigquery/
│   ├── __init__.py          # Package exports
│   ├── config.py            # BigQueryConfig model
│   ├── connector.py         # BigQueryConnector implementation
│   ├── handlers.py          # Generic Datus URI/context hooks
│   ├── skills.py            # Skill discovery and compatibility hook
│   └── skills/db-bigquery-sql/SKILL.md
├── tests/
│   ├── unit/
│   │   ├── test_config.py          # Config validation tests
│   │   └── test_connector_unit.py  # Connector unit tests
│   └── integration/
│       ├── conftest.py             # Fixtures (config, connector)
│       └── test_integration.py     # Integration tests
├── pyproject.toml
└── README.md
```

## Development

### Setup

```bash
# From the workspace root
uv sync --package datus-bigquery
```

### Code Quality

```bash
# Format code
uv run ruff format --check datus-bigquery
uv run ruff check datus-bigquery
```

## Requirements

- Python >= 3.12
- datus-db-core >= 0.1.6
- datus-sqlalchemy >= 0.1.8
- sqlalchemy-bigquery >= 1.17.2

## License

Apache License 2.0
