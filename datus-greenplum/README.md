# datus-greenplum

Greenplum database adapter for [Datus](https://github.com/Datus-ai/datus-agent).

## Timeouts

`timeout_seconds` must be positive and now bounds driver I/O as well as pool
checkout where applicable. Long operations may need a larger value. Server
execution deadlines and supported overrides are described in the shared
[timeout policy and real-database validation](../docs/adapter-timeouts.md).
A socket/RPC timeout is distinct from an overall operation deadline.

## Installation

```bash
pip install datus-greenplum
```

## Usage

```python
from datus_greenplum import GreenplumConfig, GreenplumConnector

config = GreenplumConfig(
    host="localhost",
    port=5432,
    username="gpadmin",
    password="pivotal",
    database="mydb",
)

connector = GreenplumConnector(config)
result = connector.execute({"sql_query": "SELECT 1"}, result_format="list")
print(result.sql_return)
```

## Testing

```bash
# Unit tests (no database required)
cd datus-greenplum && python -m pytest tests/unit/ -v

# Integration tests (requires running Greenplum)
cd datus-greenplum
docker compose up -d
python -m pytest tests/integration/ -v
```

The compose environment uses Greenplum 6.27.1 and exposes
`localhost:15432` with `gpadmin/pivotal` and database `test`.
