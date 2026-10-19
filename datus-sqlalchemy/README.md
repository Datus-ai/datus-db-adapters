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

## Optional connection hooks

`configure_connection(connect_args=..., url_defaults=..., connection_parameters=..., credential_version=..., connection_cleanup=..., connection_validator=...)`
adds static driver parameters and an optional dynamic provider. Each new engine,
including PostgreSQL's per-database engines, uses the same `_create_engine` path.
`url_defaults` fills missing URL query options before dialect translation. Existing
URL options win, preserving subclass-normalized CA paths. Use this for PostgreSQL
TLS and timeout options that a dialect must translate for its selected DBAPI.
Static `connect_args` override translated URL options; the dynamic provider
overrides both at every physical connection through `do_connect`. No new abstract
methods are required.

The optional `credential_version(force: bool)` callback runs before engine lookup.
A changed version disposes owned pools; `refresh_credentials()` forces this check.
Replacing hook configuration also disposes pools and must happen between operations.
The optional `connection_cleanup()` callback releases provider resources when pools
are closed or replaced. It must tolerate repeated calls and later reconnection.
The optional `connection_validator(dbapi_connection)` runs before each checkout.
It can raise SQLAlchemy `DisconnectionError` to reject a stale server role and use
the pool's bounded physical-connection retries before application SQL is sent.
This does not replay application statements.
Existing adapters that do not configure these hooks retain their creation behavior.
See [Aurora](../docs/aurora.md) for the MySQL/PostgreSQL implementation and tests.

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
