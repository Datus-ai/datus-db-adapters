# CLAUDE.md - datus-db-adapters

## Project Overview

Monorepo containing database adapters for [Datus](https://github.com/Datus-ai/datus-agent). Each adapter is an independent Python package under its own directory (e.g., `datus-mysql/`, `datus-postgresql/`).

## Git & PR Conventions

### PR Title Format (REQUIRED)

PR titles **must** start with one of these prefixes (case-insensitive), otherwise `title-check` CI will fail:

```
[BugFix], [Enhancement], [Feature], [Refactor], [UT], [Doc], [Tool], [Others]
```

Example: `[Enhancement] MySQL: Add TPC-H integration tests`

### Cross-Fork PR Creation

This repo is typically developed from a fork. Use this pattern to create PRs:

```bash
gh pr create --repo Datus-ai/datus-db-adapters \
  --head stukid:<branch-name> --base main \
  --title "[Enhancement] ..." --body "..."
```

### Pre-commit Hooks

The repo uses `ruff format` and `ruff check --fix` pre-commit hooks (same pinned ruff version as CI). When a commit fails due to auto-formatting:
1. The hooks reformat the files automatically
2. Re-stage the reformatted files: `git add <files>`
3. Create a **new** commit (do NOT amend)

## Parallel Worktree Strategy

When working on multiple adapters that each need independent branches and PRs:

- **Use `isolation: "worktree"` on Agent calls** to give each agent its own git worktree with a unique branch
- Do NOT have multiple agents share the same worktree — git only allows one branch per worktree
- Each isolated agent can independently: create branch, edit files, commit, push, and create PR

### Why Non-Isolated Parallel Agents Fail

1. **Branch conflict**: Git prohibits the same branch being checked out in multiple worktrees
2. **Lock contention**: Parallel git operations on a shared `.git` directory race on `index.lock`
3. **State corruption**: Multiple agents staging/committing in the same worktree overwrite each other

## Adapter Patterns

### SQLAlchemy-Based Adapters (MySQL, PostgreSQL, Redshift, Snowflake, ClickHouse, Spark, Hive)

- Use `Config` object (Pydantic BaseModel) + `Connector` class
- Execute via `connector.execute({"sql_query": "..."}, result_format="list")`
- Inherit from `SQLAlchemyConnector`

### SQLAlchemy-Based with Catalog Support (StarRocks, Trino)

- Same pattern as above but with catalog-level addressing
- StarRocks: extends MySQL connector, uses `ENGINE=OLAP`, `DISTRIBUTED BY HASH`, `PROPERTIES` in DDL
- Trino: uses catalog.schema.table addressing, connects via `trino://` SQLAlchemy dialect

### Independent Adapters (ClickZetta)

- Use keyword-arg constructor (not config object)
- Execute via `connector.execute_query(sql, result_format)`
- Does NOT inherit from SQLAlchemyConnector

### SQL Dialect Differences

| Database | Identifier Quoting | Schema Qualification |
|----------|-------------------|---------------------|
| MySQL | backticks `` ` `` | `` `database`.`table` `` |
| StarRocks | backticks `` ` `` | `` `catalog`.`database`.`table` `` |
| Trino | double quotes `"` | `"catalog"."schema"."table"` |
| PostgreSQL | double quotes `"` | `"schema"."table"` |
| Redshift | double quotes `"` | `"schema"."table"` |
| ClickHouse | backticks `` ` `` | `` `database`.`table` `` |
| Spark | backticks `` ` `` | `` `database`.`table` `` |
| Hive | backticks `` ` `` | `` `database`.`table` `` |
| ClickZetta | double quotes `"` | `"schema"."table"` |

## Testing

**Read [docs/adapter-testing-standard.md](docs/adapter-testing-standard.md) before writing, modifying, or reviewing adapter tests.** It defines the required layout, the unit-test file checklist, the mandatory shared contract tests (`datus_db_core.testing.contract`) and TPC-H fixtures (`datus_db_core.testing.tpch`), the skip policy, assertion quality rules, and CI registration steps. `datus-doris/` is the reference implementation.

### Test Structure Per Adapter

```
datus-<adapter>/
├── datus_<adapter>/
│   └── tpch_data.py       # Dialect DDL + shared TPC-H data wiring
├── tests/
│   ├── unit/              # Mocked tests, no database needed
│   └── integration/       # Real database tests, split by topic
│       ├── conftest.py    # Fixtures + TPC-H data setup
│       ├── test_connection.py
│       ├── test_contract.py       # Shared cross-engine contract (mandatory)
│       ├── test_sql_execution.py
│       ├── test_metadata_retrieval.py
│       └── test_tpch.py   # TPC-H benchmark tests
├── scripts/
│   └── init_tpch_data.py  # CLI for manual data init
└── docker-compose.yml     # Test container (if applicable)
```

### Running Tests

```bash
# Unit tests only (fast, no database)
cd datus-<adapter> && python -m pytest tests/unit/ -v

# Integration tests (requires running database)
cd datus-<adapter> && python -m pytest tests/integration/ -v
```

## Build & Dependencies

- Python >= 3.12
- Virtual environment at project root: `.venv/`
- Each adapter installed in editable mode: `pip install -e datus-<adapter>/`
