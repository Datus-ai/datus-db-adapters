# Datus GaussDB Adapter

Huawei GaussDB (Kernel 50x) and openGauss adapter for
[Datus](https://github.com/Datus-ai/datus-agent). Both speak the PostgreSQL
wire protocol, so the adapter builds on `datus-postgresql` and adds the pieces
that differ: the official `gaussdb` client driver, GaussDB-only system schemas,
distribution-aware DDL, and A/B/PG compatibility-mode SQL rules.

## Installation

```bash
pip install datus-gaussdb
```

## Configuration

```yaml
services:
  datasources:
    gaussdb:
      type: gaussdb
      host: ${GAUSSDB_HOST}
      port: ${GAUSSDB_PORT}
      username: ${GAUSSDB_USER}
      password: ${GAUSSDB_PASSWORD}
      database: ${GAUSSDB_DATABASE}
      schema: public
      sslmode: prefer
```

```python
from datus_gaussdb import GaussDBConfig, GaussDBConnector

config = GaussDBConfig(
    host="127.0.0.1",
    port=25434,
    username="datus",
    password="Datus@123",
    database="postgres",
)

connector = GaussDBConnector(config)
result = connector.execute({"sql_query": "SELECT 1"}, result_format="list")
print(result.sql_return)
```

The adapter accepts `table`, `schema.table`, and `database.schema.table`
identifiers.

## Authentication

| Driver | Authentication methods | When to use |
|--------|-----------------------|-------------|
| `gaussdb` (default) | sha256, md5, sm3 | Any GaussDB / openGauss server, including a stock installation |
| `psycopg2` | md5 only | Escape hatch when the official driver cannot be installed |

GaussDB defaults to `sha256` password authentication, which vanilla PostgreSQL
drivers do not implement. The default `gaussdb` driver speaks it, so a stock
server works with no server-side changes.

The `psycopg2` escape hatch is selected in the datasource config:

```yaml
      driver: psycopg2
```

It requires the server to offer md5 authentication for that login: an `md5`
rule in `pg_hba.conf` **and** a password stored as an md5 digest, which means
the role's password must have been set while `password_encryption_type = 1`
(GaussDB's md5-compatible setting). Changing that parameter does not re-encrypt
existing passwords — the role's password has to be set again afterwards.

## Vendored libpq

The official `gaussdb` driver is a psycopg3 fork that binds libpq through
ctypes, and its struct layouts match only the GaussDB/openGauss build of libpq
— loading a vanilla PostgreSQL libpq crashes the process. Wheels of this
package therefore bundle the openGauss libpq and its OpenSSL dependencies under
`datus_gaussdb/_vendor/<arch>/` and force the driver to load that copy.

The bundled binaries are redistributed under the
[Mulan Permissive Software License v2](http://license.coscl.org.cn/MulanPSL2).

Override the resolution with `DATUS_GAUSSDB_LIBPQ`:

| Value | Effect |
|-------|--------|
| unset | use the bundled library, falling back to system discovery when absent |
| `system` | skip the bundle and use normal library discovery |
| `/path/to/libpq.so` | load that library |

An installed official GaussDB client on the same host is safe: the bundle is
loaded by absolute path and nothing is added to the loader search path, so
neither installation shadows the other. Set `DATUS_GAUSSDB_LIBPQ=system` if you
would rather use the client you installed.

macOS is not supported by the driver natively — no openGauss libpq is published
for Darwin. Run the adapter (and its integration tests) inside a Linux
container on macOS hosts.

## Compatibility modes and deployment shapes

GaussDB databases are created in an `A` (Oracle), `B` (MySQL) or `PG`
compatibility mode. `A` is the common default and changes SQL semantics in ways
that matter for generated SQL — most importantly, the empty string and NULL are
the same value, so `col = ''` never matches and inserting `''` stores NULL. The
adapter ships these rules to the agent as SQL generation notes, and reports the
empty-string behaviour through its migration capabilities so that data moved
into GaussDB is not silently altered.

Centralized and distributed deployments are both supported. The connector never
parses version strings; it probes the catalog at runtime (`pgxc_class` for
distribution, `pg_database.datcompatibility` for the compatibility mode,
`pg_matviews` for materialized-view support) and caches the result per database.
On distributed deployments, generated table DDL is completed with the
`DISTRIBUTE BY` clause reconstructed from `pgxc_class`.

## Testing

Unit tests need no database:

```bash
cd datus-gaussdb && python -m pytest tests/unit/ -v
```

Integration tests need a live server. The bundled compose file starts openGauss
7.0.0-RC2 and provisions the `datus` login role:

```bash
cd datus-gaussdb
docker compose up -d          # wait for the container to report healthy
python scripts/init_tpch_data.py --drop
python -m pytest tests/integration/ -v -m integration
```

Defaults match the compose environment; override through the environment:

```bash
GAUSSDB_HOST=127.0.0.1
GAUSSDB_PORT=25434            # GAUSSDB_HOST_PORT sets the published port
GAUSSDB_USER=datus
GAUSSDB_PASSWORD=Datus@123
GAUSSDB_DATABASE=postgres
GAUSSDB_SCHEMA=public
```

Tear the environment down with `docker compose down -v`. The compose file
documents two openGauss container quirks (the mandatory out-of-datadir
`GAUSSLOG`, and the first post-initdb server start aborting on Docker Desktop
for macOS) that its entrypoint wrapper works around.

On macOS the tests themselves must also run inside a Linux container, because
the driver's libpq is Linux-only.

## Source checkouts

Wheels ship the vendored libpq; a git checkout does not. Populate it once
before running anything against a real server:

```bash
python scripts/fetch_vendor_libpq.py
```

The script extracts libpq from a digest-pinned openGauss image and replaces its
old OpenSSL build with checksum-pinned, security-maintained openEuler 22.03 LTS
SP4 packages. It supports one or both architectures
(`--arch x86_64|aarch64|all`), so Docker must be available. Exact image/RPM
provenance is recorded in `datus_gaussdb/_vendor/README.md`.
