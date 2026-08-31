# datus-dws

Huawei Cloud GaussDB(DWS) adapter for [Datus](https://github.com/Datus-ai/datus-agent).

DWS is a shared-nothing MPP analytical warehouse that speaks the PostgreSQL wire
protocol. This adapter builds on `datus-postgresql`: it reuses the psycopg2
transport unchanged and overrides only what DWS does differently — its system
schema set, its native table-definition function, and the Oracle-compatibility
semantics of ORA mode.

## Installation

```bash
pip install datus-dws
```

## Configuration

```yaml
datasources:
  dws_analytics:
    type: dws
    host: example.dws.myhuaweicloud.com
    port: 8000
    database: gaussdb
    schema: public
    username: dbadmin
    password: ${DWS_PASSWORD}
    sslmode: verify-ca
    sslrootcert: /path/to/cacert.pem
    timeout_seconds: 30
```

`host` also accepts the console's `host:port` form, in which case `port` may be
omitted. The cluster's default database is normally `gaussdb`.

## TLS

| `sslmode` | Behaviour |
|---|---|
| `prefer` (default) | Encrypts when the server offers it; upgrades automatically if the cluster enforces SSL |
| `require` | Encrypts without verifying the server certificate |
| `verify-ca` | Verifies the server certificate against `sslrootcert` |
| `verify-full` | **Cannot succeed** — see below |
| `disable` | Fails if the cluster has SSL enforcement switched on |

Two things to know about DWS certificates:

- **`verify-full` is not usable.** The default server certificate has `CN=server`
  and carries no `subjectAltName`, so hostname verification can never match a
  real endpoint. This is a property of the certificate, not a misconfiguration.
- **Use the v2 CA.** The console's `dws_ssl_cert` bundle contains both
  `v1/sslcert/cacert.pem` and `v2/sslcert/cacert.pem`. Only v2 matches the server
  certificate issuer; v1 is `CN=Huawei Equipment CA` and fails verification.

`sslrootcert` accepts either a filesystem path or inline PEM content, so a hosted
deployment can pass an uploaded certificate directly.

## Compatibility modes

DWS databases run in `ORA`, `TD` or `MySQL` compatibility mode, reported by
`pg_database.datcompatibility` and surfaced through
`DWSConnector.get_compatibility_mode()`.

ORA mode — the default for new clusters — changes expression semantics in ways
that can silently produce wrong results:

- `7/2` is `3.5` (double precision), not integer `3`.
- `''` is stored as NULL and `'' IS NULL` is true; `col = ''` never matches.
- `'a' || NULL` is `'a'`; concatenation absorbs NULL.
- `DATE` is `timestamp(0)` and `DATE - DATE` yields an `interval`.

TD and MySQL modes are not verified by this adapter's test suite.

## Table DDL

Table DDL comes from DWS's `pg_get_tabledef()`, which preserves `orientation`,
`compression`, `DISTRIBUTE BY`, partitioning, `TABLESPACE` and `TO GROUP`.

`TABLESPACE` and `TO GROUP` name objects of the source cluster and will not
replay elsewhere. Use `DWSConnector.strip_cluster_specific_clauses()` to remove
them before applying the DDL to a migration target.

## Testing

```bash
# Unit tests (no cluster required)
python -m pytest tests/unit/ -v

# Integration tests against a live cluster
export DWS_HOST=... DWS_PORT=8000 DWS_DATABASE=gaussdb \
       DWS_USERNAME=dbadmin DWS_PASSWORD=... DWS_SSLROOTCERT=/path/to/cacert.pem
python -m pytest tests/integration/ -v
```

Integration tests skip automatically when `DWS_HOST` is unset. They create
objects in a run-scoped schema and drop it on teardown.

## Verified against

DWS 9.1.0.227, storage-decoupled (compute-group mode), 3 CN + 3 DN, ORA
compatibility mode.
