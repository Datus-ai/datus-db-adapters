# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Live contract test for GaussDB compatibility-mode semantics.

Executes every probe in ``gaussdb-compat-contract.yaml`` (vendored from
osi-engine, which runs the same table from Rust) against one database per
DBCOMPATIBILITY mode and asserts the recorded expectations — semantic drift
on either side turns one shared table red in both repos.

The three mode databases are created on demand (``datus_compat_pg`` / ``_a``
/ ``_b``); creation needs a role with CREATEDB, so the whole module skips
when the configured login cannot create databases.
"""

from pathlib import Path

import pytest

from datus_gaussdb import GaussDBConfig, GaussDBConnector

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to read the vendored contract")

pytestmark = pytest.mark.integration

CONTRACT = Path(__file__).with_name("gaussdb-compat-contract.yaml")
MODE_DBS = {"PG": "datus_compat_pg", "A": "datus_compat_a", "B": "datus_compat_b"}


def _ensure_mode_databases(config: GaussDBConfig) -> None:
    """CREATE DATABASE refuses transaction blocks, so this goes through a raw
    autocommit DB-API connection instead of the connector."""
    from datus_gaussdb import _pg8000_gauss

    conn = _pg8000_gauss.connect(
        user=config.username,
        password=config.password,
        host=config.host,
        port=config.port,
        database=config.database,
        ssl_context=None,
    )
    conn.autocommit = True
    try:
        cur = conn.cursor()
        for mode, db in MODE_DBS.items():
            try:
                cur.execute(f"CREATE DATABASE {db} DBCOMPATIBILITY '{mode}'")
            except Exception as e:  # noqa: BLE001 - classify for skip-vs-fail
                message = str(e)
                if "already exists" in message:
                    continue
                # Only the expected insufficient-privilege case skips; any
                # other failure (server error, driver regression) must fail
                # the contract loudly, not hide it.
                if "permission denied" in message.lower():
                    pytest.skip(f"login lacks CREATEDB: {e}")
                raise
    finally:
        conn.close()


@pytest.fixture
def mode_connectors(config: GaussDBConfig):
    _ensure_mode_databases(config)
    connectors = {mode: GaussDBConnector(config.model_copy(update={"database": db})) for mode, db in MODE_DBS.items()}
    yield connectors
    for connector in connectors.values():
        connector.close()


def _load_probes():
    doc = yaml.safe_load(CONTRACT.read_text())
    return [pytest.param(p, id=p["id"]) for p in doc["probes"]]


def _render(cell) -> str:
    if cell is None:
        return "NULL"
    if isinstance(cell, bool):
        return str(cell).lower()
    return str(cell)


@pytest.mark.parametrize("probe", _load_probes())
def test_contract_probe(mode_connectors, probe):
    for mode, expected in probe["expect"].items():
        result = mode_connectors[mode].execute({"sql_query": probe["sql"].replace("\n", " ")}, result_format="list")
        row = result.sql_return[0]
        got = _render(next(iter(row.values())))
        assert got == str(expected), f"{probe['id']} [{mode}]: expected {expected!r}, got {got!r}"
