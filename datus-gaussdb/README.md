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
agent:
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
        # driver: pg8000  # optional; omit to use the platform default
        sslmode: verify-ca
        sslrootcert: /etc/datus/certs/gaussdb-ca.pem
```

| Field | Required | Description |
|-------|----------|-------------|
| `host`, `port` | yes | GaussDB/openGauss endpoint |
| `username`, `password` | yes | Login credentials; supported authentication depends on `driver` |
| `database` | yes | Database to connect to |
| `schema` | no | Default schema; defaults to `public` |
| `driver` | no | `gaussdb` on Linux and `pg8000` on macOS by default; `psycopg2` is an md5-only escape hatch |
| `sslmode` | no | `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, or `verify-full`; use `verify-ca` in production |
| `sslrootcert` | for explicit verification | CA bundle used by `verify-ca`/`verify-full`. Accepts a path **or** the PEM content itself — pg8000 verifies straight from memory, and for the libpq drivers the adapter writes the content to a private temp file. libpq's standard certificate locations are also honored |

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
| `gaussdb` (Linux default) | sha256, md5, sm3 | Any GaussDB / openGauss server, including a stock installation |
| `pg8000` (macOS default) | sha256, md5 | Pure Python, no libpq — any platform; SHA256/MD5-stored accounts (not SM3) |
| `psycopg2` | md5 only | Escape hatch when neither of the above can be installed |

GaussDB defaults to `sha256` password authentication, which vanilla PostgreSQL
drivers do not implement. The default `gaussdb` driver speaks it natively, so a
stock server works with no server-side changes.

The `pg8000` driver reaches the same result without libpq: it extends the
pure-Python [pg8000](https://pypi.org/project/pg8000/) driver with GaussDB's
SHA256 handshake (RFC 5802 over startup protocol 3.51) in
`datus_gaussdb/_pg8000_gauss.py`. macOS selects it automatically — the
official libpq has no Darwin build — and it can be chosen explicitly on any
platform:

```yaml
      driver: pg8000
```

All three drivers support the full libpq `sslmode` vocabulary. `verify-ca` is
the recommended production setting: it encrypts the connection and rejects a
server whose certificate is not signed by the configured CA. `verify-full`
adds hostname verification, so the certificate must also contain the exact
hostname used in `host`.

```yaml
      sslmode: verify-ca
      sslrootcert: /etc/datus/certs/gaussdb-ca.pem
```

| `sslmode` | Encryption | Certificate validation |
|-----------|------------|------------------------|
| `disable` | off | none |
| `allow` | preferred only after a non-TLS connection fails | none |
| `prefer` | preferred, but permits a non-TLS fallback | none |
| `require` | required | none |
| `verify-ca` | required | verifies the server certificate chain against `sslrootcert` |
| `verify-full` | required | verifies the chain and the server hostname |

The `pg8000` path treats `allow` like `prefer` (TLS first), because its API
cannot express libpq's plaintext-first retry order.

If the server enables TLS but does not require it, `prefer` can still connect
without TLS after negotiation failures; it does not authenticate the server.
If the server requires TLS, `prefer` normally connects with TLS, but explicitly
select `require`, `verify-ca`, or `verify-full` so the client cannot fall back
to plaintext against a differently configured endpoint. Only the two
`verify-*` modes require the server CA to be configured on the client. The
current adapter supports one-way TLS only: it accepts `sslrootcert`, but does
not expose client-certificate fields such as `sslcert` or `sslkey` for mutual
TLS.

The `psycopg2` escape hatch requires the server to offer md5 authentication
for that login: an `md5` rule in `pg_hba.conf` **and** a password stored as an
md5 digest, which means the role's password must have been set while
`password_encryption_type = 1` (GaussDB's md5-compatible setting). Changing
that parameter does not re-encrypt existing passwords — the role's password
has to be set again afterwards.

All drivers decode GaussDB booleans tolerantly: databases in `B` (MySQL)
compatibility mode render `boolean` as `'1'/'0'`, which strict PostgreSQL
parsers silently read as `False`.

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

The official `gaussdb` driver is not supported on macOS — no openGauss libpq is
published for Darwin. The adapter therefore defaults to the pure-Python
`gaussdb+pg8000` dialect on macOS. None of the GaussDB dialects replace or
monkey-patch SQLAlchemy's PostgreSQL dialects, and Linux continues to use the
official driver by default.

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

The repository runner starts an ephemeral openGauss 7.0.0-RC2 server, creates a
temporary CA and server certificate, requires TLS on the server, and runs the
positive and negative certificate-verification contracts:

```bash
ci/run-integration-tests.sh gaussdb
```

The generated certificates live in the compose volume only for that run and
are removed by `docker compose down -v`; no Huawei Cloud or other persistent
instance is required.

To run against an existing GaussDB/openGauss service instead, start the service
yourself and invoke the integration suite directly:

```bash
cd datus-gaussdb
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
GAUSSDB_DRIVER=pg8000         # platform default when unset
GAUSSDB_SSLMODE=verify-ca
GAUSSDB_SSLROOTCERT=/path/to/gaussdb-ca.pem
GAUSSDB_WRONG_SSLROOTCERT=/path/to/untrusted-ca.pem
```

Set both CA paths to run the positive trusted-CA and negative untrusted-CA TLS
contracts; tests that require a missing path are skipped.

Tear the environment down with `docker compose down -v`. The compose file
documents two openGauss container quirks (the mandatory out-of-datadir
`GAUSSLOG`, and the first post-initdb server start aborting on Docker Desktop
for macOS) that its entrypoint wrapper works around.

On macOS, integration tests use `pg8000` by default and also exercise the
`psycopg2` escape hatch in the dedicated TLS contract. To exercise the official
driver, run on Linux; `GAUSSDB_DRIVER=pg8000` selects the pure-Python path on
any platform.

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
