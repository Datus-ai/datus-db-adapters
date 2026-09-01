# Adapter Testing Standard

This document defines the testing standard every `datus-*` adapter package must follow. Read it
before writing tests for a new adapter, changing tests in an existing one, or reviewing a PR that
touches adapter tests.

Reference implementation: **`datus-doris/`** — the most complete adapter test suite in the repo.
Copy its structure and adapt the dialect-specific SQL. `datus-tidb/` is a good lighter-weight
example for adapters without catalog/materialized-view support.

Shared infrastructure this standard builds on:

- `datus-db-core/datus_db_core/testing/contract.py` — cross-engine contract cases and assertions
- `datus-db-core/datus_db_core/testing/tpch.py` — shared TPC-H tables, row counts, and inserts

## Table of contents

1. [Required layout](#1-required-layout)
2. [Unit tests](#2-unit-tests-testsunit)
3. [Integration tests](#3-integration-tests-testsintegration)
4. [Skip policy](#4-skip-policy)
5. [Assertion quality rules](#5-assertion-quality-rules)
6. [pytest config and markers](#6-pytest-config-and-markers)
7. [CI registration](#7-ci-registration)
8. [Audit checklist](#8-audit-checklist)

## 1. Required layout

```
datus-<adapter>/
├── datus_<adapter>/tpch_data.py     # dialect DDL + build_tpch_inserts() wiring
├── tests/
│   ├── __init__.py                  # also in unit/ and integration/
│   ├── conftest.py
│   ├── unit/                        # mocked, no database; CI runs -m "not integration"
│   └── integration/                 # real database; every module marked integration
│       └── conftest.py
├── scripts/init_tpch_data.py        # argparse CLI, --drop flag
└── docker-compose.yml               # self-hostable engines only, with healthcheck
```

Every file under `tests/integration/` must carry `pytestmark = pytest.mark.integration`.
Everything under `tests/` must be assert-based pytest — no print-based scripts, no ad-hoc runners.

## 2. Unit tests (`tests/unit/`)

Unit tests run without any database and must pass with `pytest tests/unit -m "not integration"`.
Mock at the engine/connection boundary, never at the method under test.

| File | Required when | Covers |
|---|---|---|
| `test_config.py` | always | defaults via one full `model_dump()` comparison; parametrized rejection tests (missing required field, wrong type, `extra_forbidden`) |
| `test_connector_unit.py` | always | init, context resolution, `full_name`/`quote_identifier`, `close()` swallowing driver errors |
| `test_registration.py` | always | registry metadata, capability set, hook identity, entry point resolves |
| `test_migration_mixin.py` | always | `describe_migration_capabilities()`, `validate_ddl()`, `suggest_table_layout()`, `map_source_type()` |
| `test_handlers.py` | `handlers.py` exists | URI builder (no credential leakage), context resolver, identifier parser |
| `test_skills.py` | `skills.py` exists | SKILL.md packaged, frontmatter stripped, `datus.skills` entry point unique and correct |

### test_config.py — one model_dump, parametrized rejections

Do NOT write one test function per field (anti-pattern: 25 single-assert functions for 25
defaults). Cover defaults with a single full-dict comparison and rejections with parametrize:

```python
def test_config_defaults():
    config = DorisConfig(host="h", username="u", password="p")
    assert config.model_dump() == {
        "host": "h", "port": 9030, "username": "u", "password": "p",
        "catalog": "internal", "database": None, "timeout": 30, ...
    }

@pytest.mark.parametrize(
    "kwargs, error_match",
    [
        ({}, "host"),                                   # missing required
        ({"host": "h", "port": "not-a-port"}, "port"),  # wrong type
        ({"host": "h", "unknown": 1}, "extra_forbidden"),
    ],
)
def test_config_rejections(kwargs, error_match):
    with pytest.raises(ValidationError, match=error_match):
        DorisConfig(username="u", password="p", **kwargs)
```

### test_connector_unit.py — mock the engine, assert on emitted SQL

Bypass the parent's connection setup, fake the engine, and assert on the SQL text the connector
emits:

```python
with patch("datus_mysql.MySQLConnector.__init__", return_value=None):
    connector = DorisConnector.__new__(DorisConnector)
    ...
    connector._engine = MagicMock()
    fake_conn = connector._engine.connect.return_value.__enter__.return_value
    connector.get_tables(catalog_name="c", database_name="d")
    executed = [str(c.args[0]) for c in fake_conn.execute.call_args_list]
    assert any("SHOW TABLES FROM" in sql for sql in executed)
```

Must cover: init/context resolution, `full_name` with and without catalog/database,
`quote_identifier` escaping, `close()` swallowing driver exceptions, `to_dict`/`get_type`.

### test_registration.py — identity, not existence

`register()` mutates global registry dicts — save and restore them, and assert hook **identity**:

```python
def test_registration_hooks():
    saved = {name: dict(getattr(connector_registry, name)) for name in _REGISTRY_ATTRS}
    try:
        register()
        assert connector_registry.get_uri_builder("doris") is build_doris_uri   # identity
        meta = connector_registry.get_metadata("doris")
        assert meta.capabilities == {"catalog", "materialized_view", ...}       # exact set
    finally:
        for name, value in saved.items():
            getattr(connector_registry, name).clear()
            getattr(connector_registry, name).update(value)
```

`assert get_uri_builder("x") is not None` is too weak — it passes when the wrong hook is
registered. Also verify the `[project.entry-points."datus.adapters"]` entry point resolves
(`importlib.metadata.entry_points(group="datus.adapters")`): a typo there makes the adapter
silently undiscoverable and nothing else catches it.

### test_migration_mixin.py — no connection needed

Migration-capability methods don't touch the connection; instantiate without `__init__`:

```python
connector = DorisConnector.__new__(DorisConnector)
caps = connector.describe_migration_capabilities()
assert caps.dialect_family == "doris"
assert connector.validate_ddl("CREATE TABLE t (...)") == []
```

### test_handlers.py — pure functions, no mocks

Required whenever the package has a `handlers.py`. Test `build_*_uri` (credentials must not leak
into the URI text), `resolve_*_context`, `parse_*_identifier` (quote escaping, malformed input).
Use small hand-written stand-in classes for config/secret objects instead of MagicMock so the
tests document the exact attributes the handlers rely on.

### test_skills.py — packaging is testable

Required whenever the package has a `skills.py`. Assert the SKILL.md file ships in the wheel
(`importlib.resources`), frontmatter is stripped from the loaded text, and the `datus.skills`
entry point is unique and points at the right directory.

### Deeper unit coverage (optional but encouraged)

`datus-doris/tests/unit/test_connector_contract.py` shows the next tier: branches integration
tests can't reach — identifier rendering with no catalog set, DDL retarget fallback, graceful
degradation (empty list) when the server errors, `_conn()` rollback paths.

## 3. Integration tests (`tests/integration/`)

All modules are marked `integration` and run via `pytest tests/integration -m integration`
against a live engine (docker-compose or cloud). Split by topic — do NOT write one monolithic
`test_integration.py`:

- `test_connection.py` — config object path, dict path, context manager
- `test_contract.py` — **mandatory**, see below
- `test_sql_execution.py` — SELECT / EXPLAIN / INSERT / UPDATE / DELETE round-trips; bad SQL returns `success is False`
- `test_metadata_retrieval.py` — `get_tables` / `get_views` / `get_schema` / `get_sample_rows`, exact-compare against objects created by a conftest fixture
- `test_tpch.py` — table list, parametrized row counts, JOIN, aggregation, `csv`/`arrow`/`pandas` result formats
- `test_catalog_operations.py`, `test_materialized_views.py` — only if the engine supports them

### conftest.py structure

Document every env var in the module docstring as a table (see
`datus-gaussdb/tests/integration/conftest.py` for the best example). Standard fixtures:

- `config` (function) — built from `<ADAPTER>_*` env vars (`<ADAPTER>_HOST/PORT/USER/PASSWORD/
  DATABASE`, plus `_CATALOG`/`_SCHEMA` where relevant) with localhost defaults matching the
  `docker-compose.yml` host ports
- `connector` (function) — yields a live connector, closes in `finally`
- `tpch_setup` (session) — creates + loads TPC-H tables, drops on teardown
- `metadata_objects_setup` (session) — creates known tables/views for metadata tests to
  exact-compare against

### Shared contract test (non-negotiable)

`test_contract.py` must call the shared contract module — never hand-roll it. One test function,
dialect SQL only, shared assertions do the rest. Abridged from
`datus-doris/tests/integration/test_contract.py` (read it in full when writing a new one):

```python
from datus_db_core.testing import contract

def test_deep_adapter_contract(connector, config):
    suffix = uuid.uuid4().hex[:8]
    table_name = f"contract_{suffix}"
    q = connector.quote_identifier
    table_ref = f"{q(catalog)}.{q(database)}.{q(table_name)}"   # engine's own qualification depth

    case = contract.TableContractCase(
        adapter_name="doris",
        table_name=table_name,
        drop_sql=f"DROP TABLE IF EXISTS {table_ref}",
        create_sql=f"CREATE TABLE {table_ref} (...8 columns...) <dialect CREATE options>",
        insert_sqls=[...two rows, values fixed by the shared row contract...],
        qualified_select_sql="SELECT ... AS id_value, ... AS mixed_value, ... ORDER BY id",
        limit_sql=f"SELECT {q('id')} AS id_value FROM {table_ref} ORDER BY {q('id')} LIMIT 1",
        schema_kwargs={"catalog_name": catalog, "database_name": database},
        expected_columns=("id", "Mixed Case", "special-name", "nullable_text",
                          "event_date", "event_ts", "amount", "bool_flag"),
        dialect_select_sqls=(...optional engine-specific SELECTs...),
    )
    contract.assert_table_contract(connector, case)
```

Non-negotiables baked into the shared assertions (`datus_db_core/testing/contract.py`):

- Columns must include a mixed-case name (`Mixed Case`) and a hyphenated name (`special-name`) —
  this is what actually exercises identifier quoting.
- Row 1 values are fixed: `1, 'Alpha', 'S-1', NULL, DATE 2024-02-03, TS 2024-02-03 04:05:06,
  123.45, TRUE` — `assert_default_contract_row` pins their round-trip types (INT, strings,
  NULL, DATE, TIMESTAMP, `Decimal`, BOOL).
- Cleanup runs in `finally`; cleanup failures attach to the original error via `add_note()`.
- Read-only engines/catalogs use `SelectContractCase` + `assert_select_contract` with literal
  SELECTs (`CAST(...)`) instead of a table — see `datus-trino` (runs on the built-in `tpch`
  catalog).

### TPC-H (non-negotiable for engines with a live test target)

Never inline or copy TPC-H data. Three pieces, no data duplication anywhere:

1. `datus_<adapter>/tpch_data.py` — dialect DDL only, everything else from the shared module
   (`datus-doris/datus_doris/tpch_data.py`):

   ```python
   from datus_db_core.testing.tpch import ROW_COUNTS, TPCH_TABLES, build_tpch_inserts

   _TPCH_DDL_ITEMS = [("tpch_region", "CREATE TABLE ..."), ...]   # 5 tables
   if [t for t, _ in _TPCH_DDL_ITEMS] != TPCH_TABLES:
       raise ValueError("DDL order must match the shared TPCH_TABLES order")
   TPCH_DDL = [ddl for _, ddl in _TPCH_DDL_ITEMS]
   TPCH_DATA = build_tpch_inserts(lambda t: f"`{t}`")             # adapter's quoting
   ```

2. `tests/integration/conftest.py::tpch_setup` — imports from `tpch_data.py`.

3. `scripts/init_tpch_data.py` — argparse CLI for manual setup: `--host/--port/--username/
   --password/--database` (+ `--schema` or `--catalog` where relevant), `--drop` to clean first.
   Imports `TPCH_DDL/TPCH_DATA/TPCH_TABLES/ROW_COUNTS` from the package's `tpch_data` — never a
   second copy of the data.

`test_tpch.py` covers: table presence in metadata, parametrized per-table row counts against
`ROW_COUNTS`, one JOIN, one aggregation, and `result_format` in `csv`/`arrow`/`pandas`.

### Metadata tests — exact comparison

Create the objects you assert on. `metadata_objects_setup` creates known tables/views, then:

```python
assert sample_rows[0] == {"identifier": "`test`.`sample_table`", "table_type": "table", ...}
assert "1,10" in sample_rows[0]["sample_rows"]
```

An adapter returning `[]` for everything must fail these tests.

### docker-compose.yml

- Map host ports through variables so local runs don't collide: `"${DORIS_HOST_PORT:-49030}:9030"`.
- Every service needs a healthcheck. Prefer a real client query (`mysql -e 'SELECT 1'`,
  `psql -c 'SELECT 1'`, `clickhouse-client --query`), fall back to TCP
  (`bash -c 'echo > /dev/tcp/localhost/9083'`) or an HTTP status endpoint.
- Slow-starting engines (Doris-class) additionally ship `scripts/wait_for_<engine>.py` used by CI.

### High-water-mark tests

The best tests in the repo assert a real engine semantic and say why it's non-obvious in the
docstring. Add at least one per adapter. Examples to imitate:

- `datus-doris/.../test_materialized_views.py::test_synchronous_materialized_view_is_not_a_separate_object`
- `datus-tidb/.../test_sql_execution.py::test_check_constraints_are_not_enforced`
- `datus-doris/.../test_metadata_retrieval.py::test_schema_reports_duplicate_key_columns_as_non_unique`

## 4. Skip policy

Single standard for the whole repo:

- Connection failure at session setup → `pytest.skip(f"<Engine> not available: {e}")` — acceptable.
- Anything after a successful connection (CREATE, INSERT, fixture provisioning) fails → **raise
  `RuntimeError`, never skip**. A green run must mean the adapter works, not that setup limped
  through. The load-bearing pattern (`datus-gaussdb/tests/integration/conftest.py`):

  ```python
  @pytest.fixture(scope="session")
  def tpch_setup():
      try:
          conn = Connector(_build_config())
          reachable = conn.test_connection()
      except Exception as e:
          pytest.skip(f"<Engine> is unavailable for TPC-H setup: {e}")
      if not reachable:
          pytest.skip("<Engine> connection test failed for TPC-H setup")

      for ddl in TPCH_DDL:
          result = conn.execute_ddl(ddl)
          if not result.success:
              raise RuntimeError(f"TPC-H DDL failed: {result.error}")   # NOT pytest.skip
      ...
  ```

- Never use `pytest.skip` inside a test body to bypass a failed operation.
- Cloud-only adapters skip on missing env vars instead of probing a connection:

  ```python
  def _required_env(name: str) -> str:
      value = os.getenv(name)
      if not value:
          pytest.skip(f"{name} is not set")
      return value
  ```

## 5. Assertion quality rules

Forbidden patterns — each one has produced silently-useless tests in this repo:

1. **Conditional assertions**: `if len(tables) > 0: assert ...` — if the test created the table,
   an empty list is a bug; assert the exact expected content unconditionally.
2. **Type-only assertions**: `assert isinstance(result, list)` as the only check — assert contents
   against fixture-created objects instead.
3. **`pytest.skip` on operation failure** inside tests (see skip policy).
4. **Tautologies**: `assert exc_info.value is not None` after `pytest.raises`.
5. **Print-based scripts** in `tests/` — everything under `tests/` must be assert-based pytest.

## 6. pytest config and markers

Copy verbatim into every adapter's `pyproject.toml`, substituting the package name in
`[tool.coverage.run] source`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
    "acceptance: marks tests as acceptance tests for CI/CD (core functionality validation)",
]
testpaths = ["tests"]

[tool.coverage.run]
source = ["datus_<adapter>"]
omit = ["tests/*", "**/__init__.py"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "class .*\\bProtocol\\):",
    "@(abc\\.)?abstractmethod",
]
```

Marker semantics:

- `integration` — the only marker CI filters on: unit runs use `-m "not integration"`, integration
  runs use `-m integration`. Every module under `tests/integration/` must carry it.
- `acceptance` — declared for local filtering of core-path tests; CI does not select on it. Apply
  it to the core happy-path tests only, not to every test.
- Do NOT declare markers a second time in a `conftest.py::pytest_configure` — the pyproject
  declaration is the single source of truth.
- Any marker used but undeclared produces `PytestUnknownMarkWarning` and breaks under
  `--strict-markers`.

Aim for ≥80% line coverage on the adapter package. CI does not currently measure coverage, so
check locally when touching an adapter:

```bash
cd datus-<name> && python -m pytest tests/unit -m "not integration" --cov=datus_<name> --cov-report=term-missing
```

## 7. CI registration

CI is change-impact driven: PRs run unit tests + package smoke only; integration targets run in
the merge queue and a weekly cron on self-hosted runners (`.github/workflows/test.yml`, contract
documented in `ci/required-checks.md`).

Steps to register `datus-<name>`:

1. **`ci/run-unit-tests.sh`** — add to `PACKAGE_SPECS`:
   `"datus-<name>:datus-<name>/tests/unit"`.
2. **`ci/integration-targets.toml`** — add a target:

   ```toml
   [targets.<name>]
   package = "datus-<name>"
   kind = "compose"          # or "cloud" for managed-only services
   ```

   If there is no live test target (no credentials, cost decision), still add the entry with
   `enabled = false` and a comment stating the reason, or add the package to
   `[selection].packages_without_live_targets`. An adapter absent from this file silently gets no
   integration coverage — the aggregate `integration-tests` gate only checks selected targets.
3. **Compose targets**: add `ci/integration/adapters/<name>.sh` (exports the env vars the tests
   read, using the compose host ports) and a readiness probe in `ci/integration/readiness/<name>.py`
   (reuse `_common.py`; a plain connector-connect probe is the default).
4. **Cloud targets**: add a reusable workflow `.github/workflows/<name>-cloud-tests.yml` called
   from `test.yml` with scoped secrets (follow `dws-cloud-tests.yml`).

Every adapter README needs a Testing section: `docker compose up -d` (or required env vars for
cloud), the two pytest commands (unit / integration), and `scripts/init_tpch_data.py` usage.
`datus-starrocks/README.md` is the reference format.

## 8. Audit checklist

To audit an existing adapter, run through in order and report gaps with file:line:

1. Layout: `tests/unit/` + `tests/integration/` split? `__init__.py` present? No stray scripts in `tests/`?
2. Unit files: all "always" files present? `test_handlers.py`/`test_skills.py` present iff source module exists?
3. `test_contract.py` imports `datus_db_core.testing.contract`? (Hand-rolled = non-compliant.)
4. TPC-H reuses `datus_db_core.testing.tpch`? No inlined/forked data?
5. Skip policy: no skip-after-connect, no skip-in-test-body?
6. Grep for forbidden assertion patterns (section 5, rules 1–4).
7. `[tool.pytest.ini_options]` present; every used marker declared?
8. CI: package mapped in `ci/run-unit-tests.sh`; listed in `ci/integration-targets.toml` (or in
   `packages_without_live_targets` with reason)?
