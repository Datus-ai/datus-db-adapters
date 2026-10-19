# Integration Tests

Integration tests require a real Hive Server2 endpoint.

## Quick Start

```bash
# Start Hive stack
docker-compose up -d

# Run integration tests
uv run pytest tests/integration/ -m integration -v

# Stop Hive stack
docker-compose down
```

## Setup

### Docker (Recommended)

Hive services are pre-configured in `docker-compose.yml`:
- HiveServer2: `localhost:10000`
- Metastore: `localhost:9083`
- Database: `default`

### Manual Setup (Alternative)

Set environment variables:
```bash
export HIVE_HOST=localhost
export HIVE_PORT=10000
export HIVE_USERNAME=hive
export HIVE_PASSWORD=
export HIVE_DATABASE=default
# Optional auth method (e.g., CUSTOM, LDAP, NONE)
export HIVE_AUTH=
# Optional JSON map of configuration values
export HIVE_CONFIGURATION_JSON='{"spark.app.name":"datus_hive_test"}'
```

## Notes

- Tests create and drop temporary tables/views.
- If INSERT is not supported, the sample-row test will be skipped.

## Troubleshooting

**Port 10000 in use?**
```bash
# Change port in docker-compose.yml
ports:
  - "10001:10000"
# Then set:
export HIVE_PORT=10001
```

**Clean slate?**
```bash
docker-compose down -v
```

## Manual Inceptor tests

See [the local environment guide](../../local/inceptor/README.md) for Compose,
initialization, NONE and Kerberos readiness, and the exact pytest commands. Set
`INCEPTOR_TEST=1` to opt into `inceptor/`. Once selected, connection and fixture
errors fail the run; they never become availability skips. This target is not
registered in CI.
