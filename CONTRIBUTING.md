# Contributing to Datus Database Adapters

Thank you for your interest in contributing to Datus Database Adapters! This document provides guidelines and requirements for contributing to this project.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Environment Setup](#development-environment-setup)
- [Code Standards](#code-standards)
- [Testing Requirements](#testing-requirements)
- [Submitting Changes](#submitting-changes)
- [Creating a New Adapter](#creating-a-new-adapter)

## Getting Started

Before contributing, please:

1. Check existing [issues](https://github.com/Datus-ai/datus-db-adapters/issues) and [pull requests](https://github.com/Datus-ai/datus-db-adapters/pulls)
2. For major changes, open an issue first to discuss your proposed changes
3. Fork the repository and create a feature branch from `main`

## Development Environment Setup

### Prerequisites

- Python >= 3.12
- `uv` (recommended) or `pip`
- `git`
- `pre-commit`

### Setting up the Development Environment

#### Using uv (Recommended)

```bash
# Clone the repository
git clone https://github.com/Datus-ai/datus-db-adapters.git
cd datus-db-adapters

# Install all adapters in development mode
uv sync

# Install pre-commit hooks
uv run pre-commit install
```

#### Using pip

```bash
# Clone the repository
git clone https://github.com/Datus-ai/datus-db-adapters.git
cd datus-db-adapters

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install development dependencies
pip install -r requirements-dev.txt

# Install adapters in editable mode
pip install -e datus-sqlalchemy
pip install -e datus-mysql
pip install -e datus-starrocks
pip install -e datus-snowflake
pip install -e datus-clickzetta
pip install -e datus-redshift

# Install pre-commit hooks
pre-commit install
```

## Code Standards

### Code Quality Tools

This project uses the following tools to ensure code quality:

- **Ruff**: Code formatting, linting, and import sorting (both pre-commit and CI pin the same
  version — see `.pre-commit-config.yaml` and `.github/workflows/python-format-check.yml`)
- **pytest**: Testing framework

### Running Code Quality Checks

#### Pre-commit Hooks (Automatic)

Pre-commit hooks will automatically run when you commit changes:

```bash
git commit -m "Your commit message"
```

#### Manual Checks

```bash
# Format code with ruff
ruff format datus-<adapter>/

# Lint (and auto-fix) with ruff
ruff check --fix datus-<adapter>/

# Run all pre-commit checks
pre-commit run --all-files
```

### Code Style Guidelines

1. **Line Length**: Maximum 120 characters
2. **Imports**: Sorted by ruff
3. **Docstrings**: Use Google-style docstrings for all public functions and classes
4. **Type Hints**: Use type hints for function signatures
5. **Comments**: Write clear, concise comments in English
6. **Naming Conventions**:
   - Classes: `PascalCase`
   - Functions/methods: `snake_case`
   - Constants: `UPPER_CASE`
   - Private methods: `_leading_underscore`

### Example Code Style

```python
from typing import Optional, Dict, Any

from datus.tools.db_tools.base import BaseSqlConnector
from datus.tools.db_tools.result import ExecuteSQLResult


class MyDatabaseConnector(BaseSqlConnector):
    """Connector for MyDatabase.

    Args:
        host: Database host address
        port: Database port number
        username: Database username
        password: Database password
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: Optional[str] = None,
    ) -> None:
        super().__init__(dialect="mydatabase")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self._connection = None

    def execute_query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> ExecuteSQLResult:
        """Execute a SQL query and return results.

        Args:
            sql: SQL query string to execute
            params: Optional query parameters

        Returns:
            ExecuteSQLResult containing query results

        Raises:
            ConnectionError: If database connection fails
            QueryError: If query execution fails
        """
        # Implementation here
        pass
```

## Testing Requirements

The complete testing standard lives in **[docs/adapter-testing-standard.md](docs/adapter-testing-standard.md)** — required layout, unit-test file checklist, the shared contract tests (`datus_db_core.testing.contract`) and TPC-H fixtures (`datus_db_core.testing.tpch`), skip policy, assertion quality rules, and CI registration. Read it before writing adapter tests; this section is only a summary. `datus-doris/` is the reference implementation.

### Test Structure

Each adapter follows this structure (see the standard for the full file-by-file checklist):

```text
datus-<adapter>/
├── datus_<adapter>/
│   └── tpch_data.py             # Dialect DDL + shared TPC-H data wiring
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/                    # Mocked tests, no database needed
│   └── integration/             # Real database tests, split by topic
│       ├── conftest.py          # Env-var config, fixtures, TPC-H setup
│       ├── test_connection.py
│       ├── test_contract.py     # Shared cross-engine contract (mandatory)
│       ├── test_sql_execution.py
│       ├── test_metadata_retrieval.py
│       └── test_tpch.py
├── scripts/init_tpch_data.py    # CLI for manual data init
└── docker-compose.yml           # Test container (self-hostable engines)
```

### Running Tests

Run tests per adapter — running all adapters in one pytest invocation causes conftest conflicts:

```bash
cd datus-<adapter>

# Unit tests (fast, no database; this is what PR CI runs)
python -m pytest tests/unit -m "not integration"

# Integration tests (requires a live database, usually: docker compose up -d)
python -m pytest tests/integration -m integration

# Coverage (aim for >= 80% on the adapter package)
python -m pytest tests/unit -m "not integration" --cov=datus_<adapter> --cov-report=term-missing
```

**CI behavior**: pull requests run unit tests and package smoke checks only. Integration tests
run in the merge queue and on a weekly schedule against the targets registered in
`ci/integration-targets.toml` (see `ci/required-checks.md`).

### Key Rules

1. **Unit tests** mock at the engine/connection boundary and must pass with no database.
2. **Integration tests** must include the shared contract test (`datus_db_core.testing.contract`)
   and, for engines with a live test target, TPC-H tests reusing `datus_db_core.testing.tpch` —
   never a private copy of the data.
3. **Skip policy**: skip only when the database is unreachable at session setup (or, for cloud
   services, when required env vars are unset). Once connected, any provisioning failure must
   raise — never `pytest.skip` around a failed operation, and never skip inside a test body.
4. **Assertions**: no conditional assertions (`if len(x) > 0: assert ...`), no type-only
   assertions (`assert isinstance(x, list)` alone) — create known objects in fixtures and
   assert their exact content.
5. **Test data**: use fixtures, never hard-code credentials; connection settings come from
   `<ADAPTER>_*` environment variables with localhost defaults matching `docker-compose.yml`.

## Submitting Changes

### Pull Request Process

1. **Create a Feature Branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Your Changes**
   - Write clean, well-documented code
   - Add or update tests as needed
   - Update documentation if applicable

3. **Run Quality Checks**
   ```bash
   # Format code
   ruff format .
   ruff check --fix .

   # Run tests
   pytest

   # Run pre-commit checks
   pre-commit run --all-files
   ```

4. **Commit Your Changes**
   ```bash
   git add .
   git commit -m "Add support for XYZ feature"
   ```

5. **Push and Create Pull Request**
   ```bash
   git push origin feature/your-feature-name
   ```

   Then create a pull request on GitHub with:
   - Clear title describing the change
   - Detailed description of what changed and why
   - References to related issues (if any)
   - Screenshots or examples (if applicable)

### Pull Request Requirements

All pull requests must:

- ✅ Pass all CI checks (format, lint, tests)
- ✅ Include appropriate tests
- ✅ Update documentation if needed
- ✅ Follow the code style guidelines
- ✅ Have a clear, descriptive title
- ✅ Include a detailed description

### CI Checks

Your pull request will automatically run the following checks:

1. **Title Check**: Ensures PR title follows conventions
2. **Format Check**: Verifies formatting, linting, and import sorting with Ruff
3. **Unit Tests**: Adapter CI runs unit tests and package smoke checks

Integration tests run in the merge queue and on a weekly schedule, not on the PR itself (see `ci/required-checks.md`).

## Creating a New Adapter

### Step 1: Choose Base Layer

Decide which base to inherit from:

- **SQLAlchemy-based databases**: Inherit from `datus-sqlalchemy`
- **Native SDK databases**: Inherit from `BaseSqlConnector`

### Step 2: Create Package Structure

```bash
# Create new adapter directory
mkdir datus-<database>
cd datus-<database>

# Create package structure
mkdir -p datus_<database>
mkdir -p tests/integration
touch datus_<database>/__init__.py
touch datus_<database>/config.py
touch datus_<database>/connector.py
touch tests/__init__.py
touch tests/test_connector.py
touch tests/test_metadata.py
touch tests/test_operations.py
touch tests/integration/__init__.py
touch tests/integration/test_integration.py
touch tests/integration/README.md
touch README.md
touch pyproject.toml
touch docker-compose.yml
```

### Step 3: Implement Core Components

#### 3.1 Configuration (`config.py`)

```python
from pydantic import BaseModel, Field


class MyDatabaseConfig(BaseModel):
    """Configuration for MyDatabase adapter."""

    host: str = Field(..., description="Database host")
    port: int = Field(default=5432, description="Database port")
    username: str = Field(..., description="Database username")
    password: str = Field(..., description="Database password")
    database: str = Field(..., description="Database name")
```

#### 3.2 Connector (`connector.py`)

```python
from typing import Optional, Dict, Any
from datus.tools.db_tools.base import BaseSqlConnector
from datus.tools.db_tools.result import ExecuteSQLResult


class MyDatabaseConnector(BaseSqlConnector):
    """Connector for MyDatabase."""

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(dialect="mydatabase")
        # Initialize connection

    def execute_query(self, sql: str) -> ExecuteSQLResult:
        """Execute SQL query."""
        # Implementation
        pass

    def get_databases(self) -> list[str]:
        """Get list of databases."""
        # Implementation
        pass

    def get_tables(self, database: str, schema: Optional[str] = None) -> list[str]:
        """Get list of tables."""
        # Implementation
        pass
```

#### 3.3 Registration (`__init__.py`)

```python
from datus.tools.db_tools import connector_registry
from .connector import MyDatabaseConnector


def register():
    """Register MyDatabase adapter with Datus."""
    connector_registry.register("mydatabase", MyDatabaseConnector)


# Auto-register when package is imported
register()
```

### Step 4: Configure `pyproject.toml`

```toml
[project]
name = "datus-mydatabase"
version = "0.1.0"
description = "MyDatabase adapter for Datus"
readme = "README.md"
requires-python = ">=3.12"
license = {text = "Apache-2.0"}
authors = [
    {name = "DatusAI", email = "support@datus.ai"}
]
keywords = ["datus", "database", "mydatabase", "adapter"]

dependencies = [
    "datus-agent>=0.2.0",
    # Add database-specific dependencies
    "mydatabase-driver>=1.0.0",
]

[project.urls]
Homepage = "https://github.com/Datus-ai/datus-db-adapters"
Repository = "https://github.com/Datus-ai/datus-db-adapters"
Issues = "https://github.com/Datus-ai/datus-db-adapters/issues"

[project.entry-points."datus.adapters"]
mydatabase = "datus_mydatabase:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["datus_mydatabase"]

[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
]
testpaths = ["tests"]
```

### Step 5: Write Documentation

Create a comprehensive `README.md` with:

- Overview and features
- Installation instructions
- Configuration examples
- Usage examples
- Limitations (if any)

### Step 6: Add Tests

Write comprehensive unit tests covering:

- Connection initialization (mocked)
- Query execution (mocked)
- Metadata retrieval (mocked)
- Error handling
- Edge cases

Add integration tests with:

- Real database connection tests (using Docker containers)
- End-to-end workflow tests
- A `tests/integration/README.md` with environment setup instructions
- Docker Compose configuration for local testing
- Proper test skipping when database is unavailable

### Step 7: Update Workspace

Add your adapter to the workspace in the root `pyproject.toml`:

```toml
[tool.uv.workspace]
members = [
    # ... existing adapters
    "datus-mydatabase"
]
```

### Step 8: Submit for Review

1. Ensure all tests pass
2. Ensure code quality checks pass
3. Update root README.md if needed
4. Submit a pull request with detailed description
5. Address Code Rabbit review comments
   - Code Rabbit will automatically review your PR
   - All review comments must be resolved before merging
   - Respond to feedback and make necessary changes

## Getting Help

If you have questions or need help:

- Open an [issue](https://github.com/Datus-ai/datus-db-adapters/issues)
- Check existing documentation
- Review example adapters in this repository

## Code of Conduct

Please note that this project follows a Code of Conduct. By participating in this project, you agree to abide by its terms.

## License

By contributing to this project, you agree that your contributions will be licensed under the Apache License 2.0.

---

Thank you for contributing to Datus Database Adapters!
