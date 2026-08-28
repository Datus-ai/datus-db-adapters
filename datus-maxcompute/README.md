# Datus MaxCompute Adapter

Alibaba Cloud MaxCompute adapter for Datus. It supports both legacy
`project.table` projects and schema-enabled `project.schema.table` projects.

## Configuration

```yaml
database:
  type: maxcompute
  database: ${MAXCOMPUTE_PROJECT}
  endpoint: ${MAXCOMPUTE_ENDPOINT}
  access_key_id: ${MAXCOMPUTE_ACCESS_KEY_ID}
  access_key_secret: ${MAXCOMPUTE_ACCESS_KEY_SECRET}
  namespace_mode: auto
```

For a three-level project, `schema` is optional and defaults to `default`.
Successful automatic detection is cached per connector. Errors other than the
specific MaxCompute response for a non-three-level project are propagated.

`namespace_mode` accepts `auto`, `two_level`, or `three_level`. Use an explicit
mode only when automatic probing is unavailable to the configured identity.
Optional settings include `quota_name`, `tunnel_endpoint`,
`timeout_seconds`, `query_timeout_seconds`, and `default_hints`.

## Testing

Unit tests use mocked PyODPS clients and do not require a MaxCompute service:

```bash
uv run --package datus-maxcompute pytest datus-maxcompute/tests/unit
```

MaxCompute is a managed cloud service, so this adapter does not rely on a local
Docker service. Real two-level and three-level coverage lives in the
`MaxCompute Cloud Tests` workflow. It is not scheduled or required by normal CI;
run it manually after configuring the protected `maxcompute-integration`
environment with these secrets:

- `MAXCOMPUTE_ENDPOINT`
- `MAXCOMPUTE_ACCESS_KEY_ID`
- `MAXCOMPUTE_ACCESS_KEY_SECRET`
- `MAXCOMPUTE_TWO_LEVEL_PROJECT`
- `MAXCOMPUTE_THREE_LEVEL_PROJECT`
