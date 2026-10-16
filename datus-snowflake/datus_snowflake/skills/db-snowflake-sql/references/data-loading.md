# Snowflake loading and unloading

## Data loading and unloading

- Use `COPY INTO table` to load from named, user, or table stages and `COPY INTO location` to unload query or table data.
- Use existing stages and file formats from metadata when available. Otherwise make the required stage, path, file format, pattern, column mapping, and copy options explicit.
- Use a `SELECT` transform inside `COPY INTO table` when files require column reordering, casting, or semi-structured extraction.
- Treat `VALIDATION_MODE`, `ON_ERROR`, `MATCH_BY_COLUMN_NAME`, `PURGE`, and `FORCE` as explicit capabilities with data-quality and idempotency consequences; do not enable them implicitly.
- Never place secrets directly in generated SQL when a storage integration, external stage, or configured credential is available.
