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
