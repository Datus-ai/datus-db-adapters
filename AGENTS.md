# AGENTS.md

Guidance for AI coding agents working in this repository.

## Repository

Monorepo of database adapters for [Datus](https://github.com/Datus-ai/datus-agent). Each adapter
is an independent Python package under its own directory (`datus-mysql/`, `datus-postgresql/`, …),
sharing `datus-db-core/` (registry, models, testing helpers) and `datus-sqlalchemy/` (SQLAlchemy
connector base). Python >= 3.12, `uv` workspace, packages installed in editable mode.

## Testing (mandatory reading)

Before writing, modifying, or reviewing any adapter tests, read
[docs/adapter-testing-standard.md](docs/adapter-testing-standard.md). It defines the required
test layout, the shared contract tests (`datus_db_core.testing.contract`) and TPC-H fixtures
(`datus_db_core.testing.tpch`), the skip policy, assertion quality rules, pytest config, and CI
registration steps. `datus-doris/` is the reference implementation.

Run tests per adapter (running all adapters together causes conftest conflicts):

```bash
cd datus-<adapter>
uv run python -m pytest tests/unit -m "not integration"   # fast, no database
uv run python -m pytest tests/integration -m integration  # needs live DB (docker compose up -d)
```

## Conventions

- **PR titles** must start with one of (case-insensitive), or the `title-check` CI fails:
  `[BugFix]`, `[Enhancement]`, `[Feature]`, `[Refactor]`, `[UT]`, `[Doc]`, `[Tool]`, `[Others]`
- **Formatting**: run `ruff format . && ruff check --fix .` before committing (pre-commit hooks
  and CI both enforce ruff, pinned to the same version).
- Development typically happens on forks; PRs target `Datus-ai/datus-db-adapters` `main`.
