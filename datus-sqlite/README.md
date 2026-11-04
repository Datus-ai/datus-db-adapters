# Datus SQLite adapter

Native sqlite3 adapter extracted from Datus-agent. Config, registry discovery,
metadata, result formats, per-connection locking and migration hints use datus-db-core.
It has no dependency on Datus-agent. Applications may continue using `db_path` or
supply canonical `uri`; all other public configuration names are preserved.

## Testing

No server is required. File lifecycle and cross-consumer regression run in
Datus-db-testkit; local package tests use temporary SQLite files.

```sh
uv run python -m pytest tests/unit -m "not integration"
uv run python -m pytest tests/integration -m integration
```
