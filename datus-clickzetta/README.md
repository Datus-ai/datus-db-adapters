# Datus ClickZetta Adapter

This package provides a [ClickZetta](https://www.singdata.com/) Lakehouse adapter for [Datus](https://github.com/Datus-ai/datus-agent), enabling seamless integration with ClickZetta analytics platform.

[ClickZetta](https://www.singdata.com/) is developed by [Singdata](https://www.singdata.com/) and [Yunqi](https://www.yunqi.tech/).

## Installation

```bash
pip install datus-clickzetta
```

## Dependencies

This adapter requires the following ClickZetta Python packages:
- `clickzetta-connector-python`
- `clickzetta-zettapark-python`

## Configuration

Configure ClickZetta connection in your Datus configuration. A complete example is available at [`examples/agent.clickzetta.yml.example`](examples/agent.clickzetta.yml.example).

```yaml
namespace:
  clickzetta_prod:
    type: clickzetta
    service: "your-service-endpoint.clickzetta.com"
    username: "your-username"
    password: "your-password"
    instance: "your-instance-id"
    workspace: "your-workspace"
    schema: "PUBLIC"  # optional, defaults to PUBLIC
    vcluster: "DEFAULT_AP"  # optional, defaults to DEFAULT_AP
    secure: false  # optional
```

### Configuration Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `service` | string | Yes | - | ClickZetta service endpoint |
| `username` | string | Yes | - | ClickZetta username |
| `password` | string | Yes | - | ClickZetta password |
| `instance` | string | Yes | - | ClickZetta instance identifier |
| `workspace` | string | Yes | - | ClickZetta workspace name |
| `schema` | string | No | "PUBLIC" | Default schema name |
| `vcluster` | string | No | "DEFAULT_AP" | Virtual cluster name |
| `secure` | boolean | No | null | Enable secure connection |
| `hints` | object | No | {} | Additional connection hints |
| `extra` | object | No | {} | Extra connection parameters |

## Features

- **Full SQL Support**: Execute queries, DDL, DML operations
- **Metadata Discovery**: Automatic discovery of databases, schemas, tables, and views
- **Volume Integration**: Read files from ClickZetta volumes
- **Sample Data**: Extract sample rows for data profiling
- **Connection Management**: Automatic connection pooling and session management

## Usage

Once installed and configured, use the ClickZetta adapter with Datus:

```python
# Execute queries
result = agent.query("SELECT * FROM my_table LIMIT 10")

# Get table information
tables = agent.get_tables("my_schema")
```

## Volume Operations

The adapter supports reading files from ClickZetta volumes:

```python
# Read a file from a volume
content = connector.read_volume_file("volume:user://my_volume", "path/to/file.yaml")

# List files in a volume directory
files = connector.list_volume_files("volume:user://my_volume", "config/", suffixes=(".yaml", ".yml"))
```

## Connection Hints

You can customize ClickZetta connection behavior using hints:

```yaml
namespace:
  clickzetta_prod:
    type: clickzetta
    # ... other connection parameters
    hints:
      sdk.job.timeout: 600
      query_tag: "Datus Analytics Query"
      cz.storage.parquet.vector.index.read.memory.cache: "true"
```

## Error Handling

The adapter provides comprehensive error handling with detailed error messages for common issues:

- Connection failures
- Authentication errors
- Query execution errors
- Schema/workspace switching limitations

## Development

### Development Mode Setup (Complete Guide)

This guide covers the complete setup from Datus agent installation to ClickZetta adapter development and testing.

#### Prerequisites
- Python 3.11+ recommended
- Git

#### Step 1: Setup Datus Agent Development Environment

```bash
# Clone the Datus agent repository
git clone https://github.com/Datus-ai/datus-agent.git
cd datus-agent

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install Datus agent in editable mode
pip install -e .
```

#### Step 2: Clone and Install ClickZetta Adapter

```bash
# From your development directory
git clone https://github.com/Datus-ai/datus-db-adapters.git
cd datus-db-adapters/datus-clickzetta

# Install ClickZetta adapter in editable mode (using the same virtual environment)
pip install -e .

# Verify installation
pip show datus-clickzetta
```

#### Step 3: Configure Environment Variables

Create a `.env` file or set environment variables:

```bash
# ClickZetta connection settings
export CLICKZETTA_SERVICE="your-service.clickzetta.com"
export CLICKZETTA_USERNAME="your-username"
export CLICKZETTA_PASSWORD="your-password"
export CLICKZETTA_INSTANCE="your-instance-id"
export CLICKZETTA_WORKSPACE="your-workspace"
export CLICKZETTA_SCHEMA="your-schema"
export CLICKZETTA_VCLUSTER="default_ap"

# LLM API keys (optional for testing)
export DASHSCOPE_API_KEY="your-dashscope-key"
export DEEPSEEK_API_KEY="your-deepseek-key"
```

#### Step 4: Create ClickZetta Configuration

In your Datus agent directory, create a ClickZetta configuration file using the provided example:

```bash
# In datus-agent directory
cp ../datus-db-adapters/datus-clickzetta/examples/agent.clickzetta.yml.example conf/agent.clickzetta.yml
```

Update `conf/agent.clickzetta.yml` with ClickZetta settings:

```yaml
agent:
  target: qwen_main  # or your preferred model
  home: .datus_home

  models:
    # Your model configurations here

  namespace:
    clickzetta:
      type: clickzetta
      service: ${CLICKZETTA_SERVICE}
      username: ${CLICKZETTA_USERNAME}
      password: ${CLICKZETTA_PASSWORD}
      instance: ${CLICKZETTA_INSTANCE}
      workspace: ${CLICKZETTA_WORKSPACE}
      schema: ${CLICKZETTA_SCHEMA}
      vcluster: ${CLICKZETTA_VCLUSTER}
      secure: false
```

#### Step 5: Start Development and Testing

**Test the adapter directly:**
```bash
# From datus-clickzetta directory
python -c "
from datus_clickzetta.connector import ClickZettaConnector
import os

connector = ClickZettaConnector(
    service=os.getenv('CLICKZETTA_SERVICE'),
    username=os.getenv('CLICKZETTA_USERNAME'),
    password=os.getenv('CLICKZETTA_PASSWORD'),
    instance=os.getenv('CLICKZETTA_INSTANCE'),
    workspace=os.getenv('CLICKZETTA_WORKSPACE'),
    schema=os.getenv('CLICKZETTA_SCHEMA'),
    vcluster=os.getenv('CLICKZETTA_VCLUSTER'),
    secure=False
)

result = connector.execute('SHOW SCHEMAS')
print(f'Connected! Found {result.row_count} schemas')
"
```

**Start Datus CLI with ClickZetta:**
```bash
# From datus-agent directory
python -m datus.cli.main --config conf/agent.clickzetta.yml --namespace clickzetta
```

#### Step 6: Development Workflow

**Making Changes:**
1. Edit code in `datus-clickzetta/datus_clickzetta/connector.py`
2. Changes are immediately available (editable install)
3. No need to reinstall the package

**Testing Changes:**
```bash
# Run unit tests (no ClickZetta account required)
cd datus-clickzetta
uv run pytest tests/unit -m "not integration" -v

# Test with Datus CLI
cd ../datus-agent
python -m datus.cli.main --config conf/agent.clickzetta.yml --namespace clickzetta
```

**Commit and Push:**
```bash
# From adapter directory
git add .
git commit -m "Your changes"
git push origin your-branch

# From agent directory (if you made agent changes)
git add .
git commit -m "Your agent changes"
git push origin your-branch
```

#### Directory Structure

```text
your-dev-folder/
├── datus-agent/                    # Datus agent repository
│   ├── .venv/                     # Shared virtual environment
│   ├── conf/agent.clickzetta.yml  # ClickZetta configuration
│   └── ...
└── datus-db-adapters/             # Adapters repository
    └── datus-clickzetta/          # ClickZetta adapter
        ├── datus_clickzetta/
        │   └── connector.py       # Main connector code
        └── ...
```

#### Tips for Development

- **Editable Installs**: Both packages are installed in editable mode, so code changes are immediate
- **Environment Variables**: Use `.env` files for local development, environment variables for production
- **Testing**: Always test both the adapter directly and through the Datus CLI
- **Debugging**: Use `logger.debug()` statements; enable with `DATUS_LOG_LEVEL=DEBUG`

#### Contributing Guidelines

1. Clone the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `uv run pytest tests/unit -m "not integration"`
5. Ensure code style compliance
6. Submit a pull request

#### Common Development Issues

**Import Errors:**
- Ensure both packages are installed in editable mode
- Check virtual environment is activated

**Connection Issues:**
- Verify environment variables are set
- Test connection with the direct connector test above

**CLI Issues:**
- Check configuration file syntax
- Verify namespace configuration matches your environment

## Testing

This adapter includes comprehensive test coverage with multiple test types and execution modes.

### Test Structure

```text
tests/
├── conftest.py               # Marker declarations
├── unit/                     # Mocked tests, no ClickZetta account needed
│   ├── test_config.py        # ClickZettaConfig validation
│   ├── test_connector_unit.py # Connector behaviour with a mocked Session
│   └── test_utils.py         # Escaping, volume URIs, DDL building
└── integration/              # Real ClickZetta account required
    ├── conftest.py           # TPC-H fixtures and test data
    └── test_tpch.py          # TPC-H benchmark tests
```

### Running Tests

Run everything from the `datus-clickzetta/` directory:

```bash
# Unit tests (no credentials, no network)
uv run pytest tests/unit -m "not integration" -v

# Integration tests (requires the CLICKZETTA_* environment variables below)
uv run pytest tests/integration -m integration -v

# A single module or pattern
uv run pytest tests/unit/test_config.py -v
uv run pytest tests/unit -k "volume" -v
```

### TPC-H Integration Tests

TPC-H integration tests use a simplified TPC-H dataset (5 tables: region, nation, customer, orders, supplier) to validate end-to-end query execution, JOIN operations, aggregations, and multi-format output.

```bash
# Set ClickZetta credentials
export CLICKZETTA_SERVICE="your-service.clickzetta.com"
export CLICKZETTA_USERNAME="your-username"
export CLICKZETTA_PASSWORD="your-password"
export CLICKZETTA_INSTANCE="your-instance"
export CLICKZETTA_WORKSPACE="your-workspace"

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

### Test Requirements

- **Unit Tests**: No external dependencies, the ClickZetta `Session` is mocked
- **Integration Tests (TPC-H)**: Require actual ClickZetta credentials

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKZETTA_SERVICE` | (required) | ClickZetta service endpoint |
| `CLICKZETTA_USERNAME` | (required) | ClickZetta username |
| `CLICKZETTA_PASSWORD` | (required) | ClickZetta password |
| `CLICKZETTA_INSTANCE` | (required) | ClickZetta instance ID |
| `CLICKZETTA_WORKSPACE` | (required) | ClickZetta workspace |
| `CLICKZETTA_SCHEMA` | `PUBLIC` | Default schema |
| `CLICKZETTA_VCLUSTER` | `DEFAULT_AP` | Virtual cluster |

### Test Coverage

- Configuration validation and error handling
- SQL query execution and result processing
- Metadata discovery (tables, views, schemas)
- Connection management and lifecycle
- Volume operations and file listing
- Error handling and exception cases
- TPC-H benchmark queries (JOINs, aggregations, multi-format output)

## License

This project is licensed under the Apache 2.0 License - see the [LICENSE](../LICENSE) file for details.

## Support

For issues and questions:
- [GitHub Issues](https://github.com/Datus-ai/datus-db-adapters/issues)
- [Datus Documentation](https://docs.datus.ai/)
- [ClickZetta Documentation](https://www.yunqi.tech/documents)