# Datus DuckDB adapter

Native DuckDB connector, independently installable without Datus-agent.
The initial implementation is extracted from Datus-agent `0facfb63467f0178d4e053b381cedd3a203fa408`;
the agent still uses its existing built-in implementation during the review milestone.

```python
from datus_db_core import connector_registry

config = connector_registry.validate_config("duckdb", {"uri": "warehouse.duckdb"})
with connector_registry.create_connector("duckdb", config) as db:
    result = db.execute_query("SELECT 1 AS value", result_format="list")
    assert result.success
```

The registry exports the same `DuckDBConfig` used by the connector. `uri` accepts
an ordinary path, a `duckdb:///` URI, or `:memory:`; `db_path` remains an input
alias. Supplying both is rejected rather than silently discarding one. File creation
and `read_only=False` defaults are unchanged. A read-only connection requires an
existing database. Iceberg, extension settings, locking, Arrow/pandas results, and
migration methods remain part of the connector.

## Testing

No Docker service is required. Run packages separately:

```bash
uv run --package datus-duckdb --extra test python -m pytest datus-duckdb/tests/unit -m 'not integration'
uv run --package datus-duckdb --extra test python -m pytest datus-duckdb/tests/integration -m integration
```

Integration tests create disposable local files. Shared business datasets and
adapter/dosi regression are owned by `datus-db-testkit`; this package retains
implementation regressions and the common adapter contract. There is no separate
TPC-H loader in this first extraction milestone.
