# datus-tidb

TiDB database adapter for [Datus](https://github.com/Datus-ai/datus-agent).

TiDB speaks the MySQL wire protocol, so this adapter builds on `datus-mysql` and
overrides only what genuinely differs: the `METRICS_SCHEMA` system database,
the absence of materialized views, TiFlash columnar replicas, and the two DDL
clauses TiDB accepts without honouring.

## Installation

```bash
pip install datus-tidb
```

## Configuration

```yaml
namespace:
  my_tidb:
    type: tidb
    host: 127.0.0.1
    port: 4000          # TiDB's own default; 3306 is a different server
    username: root
    password: ""
    database: analytics
```

| Key | Default | Notes |
|---|---|---|
| `host` | `127.0.0.1` | |
| `port` | `4000` | TiDB's built-in default (`tidb-server -P`) |
| `username` | — | Required |
| `password` | `""` | |
| `database` | `None` | Default database |
| `charset` | `utf8mb4` | |
| `autocommit` | `true` | |
| `timeout_seconds` | `30` | |

TLS is not supported yet: the semantic layer's MySQL-wire executor has no TLS
either, so accepting the keys here would let a datasource connect for metadata
and then fail at query time. TiDB Cloud endpoints that require TLS are
therefore out of reach for now.

## Usage

```python
from datus_tidb import TiDBConfig, TiDBConnector

connector = TiDBConnector(TiDBConfig(username="root", database="analytics"))
result = connector.execute({"sql_query": "SELECT 1"}, result_format="list")
```

## TiFlash

TiFlash is TiDB's columnar replica engine. A table reaches it only after
`ALTER TABLE t SET TIFLASH REPLICA 1` finishes syncing; from then on the
optimizer picks between row store (TiKV) and columnar (TiFlash) on its own, and
analytical queries run in parallel MPP mode without any query change.

Replica state is one query — `SELECT * FROM information_schema.TIFLASH_REPLICA`
— which is what the packaged SQL skill points the model at; the adapter adds no
wrapper around it.

Note that **window functions largely do not run in MPP**: only `ROW_NUMBER`,
`RANK`, `DENSE_RANK`, `LEAD`, `LAG`, `FIRST_VALUE` and `LAST_VALUE` push down.
Aggregate window functions (`SUM`/`AVG`/`COUNT` over a window), `STDDEV_*`,
`VAR_*`, `NTILE`, `PERCENT_RANK`, `CUME_DIST` and `NTH_VALUE` fall back to
single-node computation on the TiDB layer — results stay correct, parallelism is
lost. The packaged SQL skill tells the model to prefer `GROUP BY` aggregation
where an equivalent exists.

## Known TiDB behaviors

| Construct | Behavior |
|---|---|
| `CHECK` constraints | Parsed, **not enforced** unless `tidb_enable_check_constraint=ON` |
| `FULLTEXT` indexes | Accepted, then **silently dropped**; `MATCH ... AGAINST` is rejected |
| `FULL OUTER JOIN` | Not supported |
| `JSON_TABLE`, `LATERAL`, `CREATE TABLE ... AS SELECT` | Not supported |
| `CORR`, `COVAR_*` | Do not exist |
| Materialized views | Do not exist |
| Views | Read-only |
| Default collation | `utf8mb4_bin` — case-sensitive, unlike MySQL 8 |

`validate_ddl()` flags the first two, which are the ones that fail silently.

## Testing

```bash
# Unit tests (no database)
pytest datus-tidb/tests/unit -v

# Integration tests (four-container cluster with TiFlash)
docker compose -f datus-tidb/docker-compose.yml up -d --wait
pytest datus-tidb/tests/integration -m integration -v
docker compose -f datus-tidb/docker-compose.yml down -v
```

The compose file runs PD, TiKV, TiDB and TiFlash rather than the
single-container `--store=unistore` form: unistore starts in about a second but
has no TiFlash, so it cannot cover the columnar and MPP paths this adapter
exposes.

## License

Apache License 2.0
