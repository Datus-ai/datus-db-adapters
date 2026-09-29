# datus-sqlalchemy

Base SQLAlchemy connector for Datus database adapters.

## Overview

`datus-sqlalchemy` provides a common SQLAlchemy-based connector foundation for database adapters in the Datus ecosystem. It is not a standalone database adapter but serves as a shared base class for adapters like MySQL, PostgreSQL, and other SQLAlchemy-compatible databases.

## Features

- SQLAlchemy engine and connection management
- Unified error handling and exception mapping
- Support for multiple result formats (pandas, arrow, csv, list)
- Connection pooling and lifecycle management
- Streaming query execution
- Metadata retrieval methods

## Installation

```bash
pip install datus-sqlalchemy
```

Note: This package is typically installed as a dependency of specific database adapters (e.g., `datus-mysql`).

## Usage

This package is intended to be used as a base class for database adapters:

```python
from datus_sqlalchemy import SQLAlchemyConnector

class MyDatabaseConnector(SQLAlchemyConnector):
    def __init__(self, host, port, user, password, database):
        connection_string = f"mydb://{user}:{password}@{host}:{port}/{database}"
        super().__init__(connection_string, dialect="mydb")
```

## Requirements

- Python >= 3.12
- datus-agent >= 0.2.2
- sqlalchemy >= 2.0.23
- pyarrow >= 14.0.0
- pandas >= 2.1.4

## License

Apache License 2.0

## Related Packages

- `datus-mysql` - MySQL database adapter
- `datus-starrocks` - StarRocks database adapter
- `datus-snowflake` - Snowflake database adapter (uses native connector)
