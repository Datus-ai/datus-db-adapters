# datus-postgresql

PostgreSQL database adapter for Datus.

## Installation

```bash
pip install datus-postgresql
```

## Usage

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

# Test connection
connector.test_connection()

# Execute queries
result = connector.execute({"sql_query": "SELECT * FROM users"})
```

## pgvector Runtime Requirements

`datus-postgresql` also provides a vector backend (`pgvector`) for Datus storage. For vector workloads:

1. Enable extension in target database:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

2. Apply DDL manually (runtime auto-create is not enabled):

- `datus_postgresql/ddl/all.sql`
- or generated SQL from `datus_postgresql.storage_ddl`

3. Ensure Datus storage namespace points to this PostgreSQL connection.

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| host | str | "127.0.0.1" | PostgreSQL server host |
| port | int | 5432 | PostgreSQL server port |
| username | str | required | PostgreSQL username |
| password | str | "" | PostgreSQL password |
| database | str | None | Default database name |
| schema | str | "public" | Default schema name |
| sslmode | str | "prefer" | SSL mode |
| timeout_seconds | int | 30 | Connection timeout |
| enable_vector_search | bool | true | Metadata flag for pgvector-capable namespace config |

## Troubleshooting

- `type "vector" does not exist`:
  - `vector` extension is not enabled in the target database.
- `Table <name> missing columns`:
  - Existing table schema is out of sync with current DDL.
  - Re-apply `ddl/all.sql` or run a migration.
- Upsert failure on `ext_knowledge`:
  - Ensure `ext_knowledge.id` exists and `UNIQUE (namespace, id)` is present.

## Extension Guide

Repository-level guidance for PostgreSQL + other vector backend extension patterns:

- `../docs/vector_backend_extensions.md`

## License

Apache-2.0
