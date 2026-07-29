# Datus Hologres Adapter

Alibaba Cloud Hologres adapter for Datus. It uses the PostgreSQL wire protocol
while preserving Hologres-specific namespaces, table properties, and SQL rules.

## Installation

```bash
pip install datus-hologres
```

## Configuration

```yaml
services:
  datasources:
    hologres:
      type: hologres
      host: ${HOLOGRES_HOST}
      port: ${HOLOGRES_PORT}
      username: ${HOLOGRES_ACCESS_KEY_ID}
      password: ${HOLOGRES_ACCESS_KEY_SECRET}
      database: ${HOLOGRES_DATABASE}
      schema: public
      sslmode: prefer
```

`access_key_id` and `access_key_secret` are also accepted as aliases for
`username` and `password`. You can pass a console endpoint either as a hostname
or as `hostname:port`; an explicitly configured `port` must match an embedded
endpoint port. Hologres public endpoints normally use port 80. Use the SSL mode
configured on the target Hologres instance.

The adapter supports `table`, `schema.table`, and
`database.schema.table` identifiers. It filters Hologres internal schemas from
normal metadata listings and includes Hologres storage properties in generated
table DDL.

## Tests

Unit tests do not require cloud credentials:

```bash
uv run --all-packages --with pytest pytest datus-hologres/tests/unit -m "not integration"
```

Live tests require the following environment variables:

```bash
HOLOGRES_HOST=
HOLOGRES_PORT=80
HOLOGRES_DATABASE=
HOLOGRES_SCHEMA=public
HOLOGRES_ACCESS_KEY_ID=
HOLOGRES_ACCESS_KEY_SECRET=
HOLOGRES_SSLMODE=prefer
# Set to true only for an instance type that supports materialized views.
HOLOGRES_TEST_MATERIALIZED_VIEW=false
# Optional second database for cross-database schema-routing coverage.
HOLOGRES_SECONDARY_DATABASE=
HOLOGRES_SECONDARY_SCHEMA=public
# Optional preconfigured schema.table or database.schema.table foreign table.
HOLOGRES_TEST_FOREIGN_TABLE=
```

The integration suite creates an isolated temporary schema, loads a deterministic
Tiny TPC-H fixture, and removes the schema when the test session finishes.
