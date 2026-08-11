# Datus Database Adapters

Independent database adapter packages for Datus Agent.

## Overview

This repository contains database adapter implementations for Datus Agent, using a plugin architecture where each database is an independent package that can be installed on demand.

## Architecture

```
datus-agent (Core package - SQLite/DuckDB only)
    ↓
Plugin Adapters (Independent packages, install as needed)
├── datus-sqlalchemy (SQLAlchemy base layer)
│   ├── datus-mysql
│   ├── datus-starrocks
│   ├── datus-postgresql
│   ├── datus-hologres
│   ├── datus-doris
│   └── datus-oracle
│
└── Native SDK Adapters
    ├── datus-snowflake
    ├── datus-clickzetta
    ├── datus-maxcompute
```

## Implemented Adapters

### 1. datus-sqlalchemy
SQLAlchemy base connector providing shared functionality for SQLAlchemy-based adapters.

**Installation**:
```bash
pip install datus-sqlalchemy
```

**Features**:
- SQLAlchemy engine and connection management
- Unified error handling
- Multiple result format support
- Connection pool management
- Metadata retrieval

---

### 2. datus-mysql
MySQL database adapter.

**Installation**:
```bash
pip install datus-mysql
```

**Features**:
- Full MySQL support
- INFORMATION_SCHEMA metadata queries
- SHOW CREATE statement support
- Complete CRUD operations

---

### 3. datus-starrocks
StarRocks database adapter (MySQL protocol compatible).

**Installation**:
```bash
pip install datus-starrocks
```

**Features**:
- Inherits all MySQL functionality
- Multi-Catalog support
- Materialized view support
- StarRocks-specific optimizations

---

### 4. datus-doris
Apache Doris database adapter (MySQL protocol compatible).

**Installation**:
```bash
pip install datus-doris
```

**Features**:
- Inherits MySQL query and connection functionality
- Multi-Catalog support with the built-in `internal` catalog
- Asynchronous materialized view metadata and DDL support
- Doris-specific migration and table-layout guidance

---

### 5. datus-snowflake
Snowflake database adapter (native SDK).

**Installation**:
```bash
pip install datus-snowflake
```

**Features**:
- Native Snowflake SDK
- Multi-database and schema support
- Tables, views, and materialized views
- Efficient SHOW commands
- Arrow format support

---

### 6. datus-clickzetta
ClickZetta Lakehouse database adapter (native SDK).

**Installation**:
```bash
pip install datus-clickzetta
```

**Features**:
- Native ClickZetta ZettaPark SDK
- Full SQL support (DDL, DML, DQL)
- Workspace and schema management
- Volume/Stage file operations
- Metadata discovery
- Connection pooling and session management

---

### 7. datus-maxcompute
Alibaba Cloud MaxCompute adapter (PyODPS).

**Installation**:
```bash
pip install datus-maxcompute
```

**Features**:
- Automatic two-level (`project.table`) and three-level (`project.schema.table`) namespace detection
- SQL execution through MaxCompute jobs with explicit per-project namespace hints
- Instance Tunnel reads in Arrow, pandas, list, and CSV formats, with bounded CSV iteration
- Tables, views, materialized views, DDL, columns, partitions, and sample-row metadata
- Dedicated cloud integration coverage for both namespace models

---

### 8. datus-hologres
Alibaba Cloud Hologres adapter (PostgreSQL wire protocol).

**Installation**:
```bash
pip install datus-hologres
```

**Features**:
- Database, schema, and table namespace support
- Hologres internal and foreign table discovery
- Hologres storage properties in generated DDL
- PostgreSQL-compatible SQL execution with Hologres transaction safeguards
- Tiny TPC-H cloud integration coverage on a real Hologres instance

---

### 9. datus-oracle
Oracle Database adapter using python-oracledb Thin mode.

**Installation**:
```bash
pip install datus-oracle
```

**Features**:
- Oracle 19c-compatible schema, metadata, query, and transfer behavior
- Optional `DialectOperations` for row limits, count queries, types, identifiers, and batch writes
- Adapter-packaged Oracle SQL skill with an Agent 0.3.9 notes compatibility bridge
- Docker integration coverage with an unprivileged Oracle user

---

## Development Guide

### Development Environment Setup

This repository uses workspace configuration to manage multiple adapter packages:

```bash
# Clone repository
git clone https://github.com/your-org/datus-db-adapters.git
cd datus-db-adapters

# Install all adapters (development mode)
uv sync

# Or use pip
pip install -e datus-sqlalchemy
pip install -e datus-mysql
pip install -e datus-starrocks
pip install -e datus-doris
pip install -e datus-snowflake
pip install -e datus-maxcompute
pip install -e datus-hologres
pip install -e datus-oracle
```

**Note**: The root `pyproject.toml` is only for development environment management. End users should install individual packages.

### Creating a New Adapter

1. **Choose Base Layer**:
   - If database is SQL standard compatible, inherit from `datus-sqlalchemy`
   - If native SDK is needed, inherit directly from `BaseSqlConnector`

2. **Create Package Structure**:
```bash
datus-<database>/
├── datus_<database>/
│   ├── __init__.py
│   ├── config.py
│   └── connector.py
├── tests/
├── pyproject.toml
└── README.md
```

3. **Implement Connector**:
```python
from datus.tools.db_tools.base import BaseSqlConnector

class MyDatabaseConnector(BaseSqlConnector):
    def __init__(self, ...):
        super().__init__(dialect="mydatabase")
        # Initialize connection

    # Implement required abstract methods
    def execute_query(self, sql: str) -> ExecuteSQLResult:
        ...
```

4. **Configure Entry Point**:
```toml
[project.entry-points."datus.adapters"]
mydatabase = "datus_mydatabase:register"
```

5. **Registration Function**:
```python
# __init__.py
from datus.tools.db_tools import connector_registry
from .connector import MyDatabaseConnector

def register():
    connector_registry.register("mydatabase", MyDatabaseConnector)

register()
```

## Using with Datus Agent

Adapters are automatically discovered via entry points:

```yaml
# config.yaml
namespace:
  mydatabase:
    type: mysql  # or snowflake, starrocks, etc.
    host: localhost
    port: 3306
    username: root
    password: password
    database: mydb
```

Datus Agent will automatically load the corresponding adapter.

---


## Contributing

Contributions of new database adapters are welcome!

1. Fork this repository
2. Create a new adapter package
3. Add tests and documentation
4. Submit a Pull Request

---

## License

Apache License 2.0
