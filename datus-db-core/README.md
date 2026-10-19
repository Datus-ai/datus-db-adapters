# datus-db-core

Core database abstractions for Datus adapters. Provides `BaseSqlConnector`, `ConnectorRegistry`, SQL parsing utilities, and shared models used by all database adapter packages.

## Installation

```bash
pip install datus-db-core
```

## Optional native SQL analysis

Adapters whose execution SDK does not expose a local SQLGlot AST can register
`sql_analyzer=callable` alongside existing parser/identifier hooks. The callable
accepts the original SQL and returns `datus_db_core.sql_analysis.SQLAnalysis`:

- `status`: `complete`, `unsupported`, `error`, or `incomplete`.
- `sql_type`: a `SQLType`; usable for dispatch only when analysis is complete.
- `tables`: SQL identifiers with case and quoting preserved. Empty means no
  physical tables only for a complete analysis.
- `diagnostic`: a safe explanation without SQL, credentials, or bound values.

Consumers use `connector_registry.get_sql_analyzer(dialect)` and
`analysis.require_complete()` before trusting table facts. The default is
`None`, retaining existing behavior. This is not a generator/transpilation or
lineage contract. A compatible `parser_dialect` must not replace the adapter's
namespace capabilities. No SDK dependencies or new abstract methods are added
to other adapters. This optional API is introduced in db-core 0.1.8.
