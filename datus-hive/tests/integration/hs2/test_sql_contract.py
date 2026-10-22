"""Opt-in shared SQL contracts; an explicitly selected HS2 profile fails closed.

HS2_TEST_PROFILE selects the checked-in version/configuration matrix.
DOSI_TEST_HIVE_CONNECTIONS supplies external credentials, and optional
DOSI_TEST_HIVE_CONNECTION selects a datasource (default: hive).
"""

import os
from pathlib import Path

import pytest

from datus_db_core.testing.hs2 import Environment, selected_cases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("HS2_TEST_PROFILE"), reason="Select HS2_TEST_PROFILE to run dedicated HS2 contracts"
    ),
]
ROOT = Path(__file__).resolve().parents[2] / "fixtures/hs2"
CASES = [(case, fmt) for case in selected_cases(ROOT, "native") for fmt in case.get("formats", ["list"])]


@pytest.fixture(scope="module")
def hs2_environment():
    environment = Environment(ROOT, "python-adapter")
    try:
        yield environment
    finally:
        environment.close()


@pytest.mark.parametrize("case,result_format", CASES, ids=[f"{c['id']}-{fmt}" for c, fmt in CASES])
def test_native_sql_contract(hs2_environment, case, result_format):
    hs2_environment.native(case, result_format)


def test_metadata_matches_owned_objects(hs2_environment):
    environment = hs2_environment
    record = {"case": "metadata.view_rows", "check": "metadata_api", "status": "failed"}
    environment.checks.append(record)
    database = environment.ensure("metadata")
    connector = environment.connector
    columns = connector.get_schema(table_name="orders", database_name=database)
    assert {column["name"].casefold() for column in columns} == {
        "order_id",
        "label",
        "order_date",
        "amount",
        "customer_id",
    }
    tables = connector.get_tables(database_name=database)
    views = connector.get_views(database_name=database)
    assert set(tables) == {"orders", "customers", "clock"}
    assert set(views) == {"orders_view"}
    record["status"] = "passed"
