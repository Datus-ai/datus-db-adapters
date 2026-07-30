# datus-doris

Apache Doris database adapter for Datus.

## Features

- MySQL-protocol connection and SQL execution
- Multi-catalog discovery and context switching
- Catalog-aware table, view, schema, and sample-row metadata
- Asynchronous materialized-view discovery and DDL retrieval
- Three-part identifiers: `catalog.database.table`
- Doris migration capability, table-layout, and type-mapping guidance
- List, CSV, Pandas, and Arrow result formats inherited from `datus-mysql`

## Installation

```bash
pip install datus-doris
```

The package installs `datus-db-core` and `datus-mysql` as dependencies and
registers the `doris` adapter through the `datus.adapters` entry-point group.

## Configuration

```yaml
database:
  type: doris
  host: localhost
  port: 9030
  username: root
  password: ""
  catalog: internal
  database: analytics
```

The built-in catalog is `internal`. External catalogs, including Hive Metastore
catalogs, can be selected through the same connector API.

## Python API

```python
from datus_doris import DorisConfig, DorisConnector

config = DorisConfig(
    host="localhost",
    port=9030,
    username="root",
    password="",
    catalog="internal",
    database="analytics",
)

with DorisConnector(config) as connector:
    catalogs = connector.get_catalogs()
    tables = connector.get_tables(database_name="analytics")
    views = connector.get_views(database_name="analytics")
    materialized_views = connector.get_materialized_views_with_ddl(
        database_name="analytics"
    )
    result = connector.execute_query("SELECT 1")
```

Catalog context can be switched explicitly:

```python
connector.switch_catalog("hive_catalog")
databases = connector.get_databases()
connector.switch_catalog("internal")
```

Fully qualified identifiers are rendered as Doris three-part names:

```python
connector.full_name(
    catalog_name="internal",
    database_name="analytics",
    table_name="events",
)
# `internal`.`analytics`.`events`
```

## Development and testing

Unit tests do not require a database:

```bash
ci/run-unit-tests.sh datus-doris
```

The repository integration runner starts Apache Doris FE/BE 4.0.7 and Hive
Metastore 4.0.1, waits for an alive backend and a successful OLAP DDL probe,
runs the complete integration suite, and removes the test services afterward:

```bash
ci/run-integration-tests.sh doris
```

To run the package-local workflow manually:

```bash
cd datus-doris
docker compose up -d --wait
./scripts/test.sh integration
docker compose down -v
```

The integration suite covers catalog operations, Hive external catalogs,
tables, views, asynchronous materialized views, DDL/DML, sample rows, TPC-H
queries, and list/CSV/Pandas/Arrow output.

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `DORIS_HOST` | `localhost` | FE query host |
| `DORIS_PORT` | `9030` | MySQL protocol port |
| `DORIS_USER` | `root` | Username |
| `DORIS_PASSWORD` | empty | Password |
| `DORIS_CATALOG` | `internal` | Initial catalog |
| `DORIS_DATABASE` | `test` | Test database |
| `DORIS_FE_JAVA_XMS` | `1024m` | Initial FE JVM heap for the Docker test environment |
| `DORIS_FE_JAVA_XMX` | `2048m` | Maximum FE JVM heap for the Docker test environment |
| `HIVE_METASTORE_URI` | `thrift://hive-metastore:9083` | Hive catalog metastore URI |

## License

Apache License 2.0
