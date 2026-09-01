# ClickZetta Adapter Tests

Layout and conventions follow [`docs/adapter-testing-standard.md`](../../docs/adapter-testing-standard.md).

```text
tests/
├── conftest.py                 # Marker declarations
├── unit/                       # Mocked, no ClickZetta account or network
│   ├── test_config.py          # ClickZettaConfig validation
│   ├── test_connector_unit.py  # Connector behaviour with a mocked Session
│   └── test_utils.py           # Escaping, volume URIs, DDL building
└── integration/                # Real ClickZetta account required
    ├── conftest.py             # TPC-H fixtures and test data
    └── test_tpch.py            # TPC-H benchmark tests
```

## Running

From the `datus-clickzetta/` directory:

```bash
# Unit tests — what CI runs on every PR
uv run pytest tests/unit -m "not integration" -v

# Integration tests — needs the CLICKZETTA_* variables from the adapter README
uv run pytest tests/integration -m integration -v
```

## Markers

- `integration` — every module under `tests/integration/` carries it via `pytestmark`; CI unit runs
  deselect it with `-m "not integration"`.
- `requires_clickzetta` — tests that need live ClickZetta credentials.

## Conventions

- Everything under `tests/` is assert-based pytest: no print-based scripts, no ad-hoc runners.
- Unit tests patch `datus_clickzetta.connector.Session` and assert on the SQL the connector emits;
  they must not read `CLICKZETTA_*` environment variables.
- TPC-H data lives in `tests/integration/conftest.py` and `scripts/init_tpch_data.py` — see the
  adapter README for the table list and row counts.
