# datus-bigquery

BigQuery database adapter for Datus.

## Installation

```bash
pip install datus-bigquery
```

This will automatically install the required dependencies:
- `datus-agent`
- `datus-sqlalchemy`
- `sqlalchemy-bigquery`
- `google-cloud-bigquery`

## Usage

The adapter is automatically registered with Datus when installed. Configure your database connection in your Datus configuration:

```yaml
database:
  type: bigquery
  project: your-gcp-project-id
  dataset: your_dataset
  credentials_path: /path/to/service-account.json
  location: US
```

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
result = connector.execute({"sql_query": "SELECT * FROM `your_dataset.your_table` LIMIT 10"})
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
- Metadata retrieval (tables, views, schemas)
- Sample data extraction
- Multiple result formats (pandas, arrow, csv, list)
- Project and dataset level navigation
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
│   └── connector.py         # BigQueryConnector implementation
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
# Install dependencies
uv sync

# Install in editable mode
uv pip install -e .
```

### Code Quality

```bash
# Format code
black datus_bigquery tests
isort datus_bigquery tests

# Lint
ruff check datus_bigquery tests
flake8 datus_bigquery tests
```

## Requirements

- Python >= 3.12
- datus-agent > 0.2.1
- datus-sqlalchemy >= 0.1.0
- sqlalchemy-bigquery >= 1.9.0
- google-cloud-bigquery >= 3.0.0

## License

Apache License 2.0
