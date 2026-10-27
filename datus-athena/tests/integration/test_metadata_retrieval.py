import csv
import io
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError

from datus_db_core.testing.tpch import TPCH_TABLES

pytestmark = pytest.mark.integration


def test_catalog_database_and_table_metadata(connector, config):
    assert config.catalog in connector.get_catalogs()
    assert config.database in connector.get_databases()
    assert set(TPCH_TABLES) <= set(connector.get_tables())
    schema = connector.get_schema(table_name="tpch_orders")
    assert [c["name"] for c in schema] == ["o_orderkey", "o_custkey", "o_orderstatus", "o_totalprice", "o_orderdate"]
    ddl = connector.get_tables_with_ddl(tables=["tpch_orders"])
    assert len(ddl) == 1 and ddl[0]["table_name"] == "tpch_orders"
    assert "o_orderkey" in ddl[0]["definition"]
    assert "tpch_region_view" in connector.get_views()
    view_ddls = connector.get_views_with_ddl()
    assert len(view_ddls) == 1 and view_ddls[0]["table_name"] == "tpch_region_view"
    assert "r_regionkey" in view_ddls[0]["definition"].lower()


def test_sampling(connector):
    samples = connector.get_sample_rows(tables=["tpch_region"], top_n=5)
    assert len(samples) == 1 and samples[0]["table_name"] == "tpch_region"
    rows = list(csv.DictReader(io.StringIO(samples[0]["sample_rows"])))
    assert {row["r_name"] for row in rows} == {"AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"}


def test_targeted_ddl_omits_absent_tables_without_losing_existing_definitions(connector):
    missing = "absent_" + uuid4().hex
    assert connector.get_tables_with_ddl(tables=[missing]) == []
    entries = connector.get_tables_with_ddl(tables=[missing, "tpch_orders"])
    assert [entry["table_name"] for entry in entries] == ["tpch_orders"]
    assert "o_orderkey" in entries[0]["definition"]


def test_targeted_ddl_does_not_hide_absent_database(connector):
    with pytest.raises(ClientError):
        connector.get_tables_with_ddl(database_name="absent_" + uuid4().hex, tables=["orders"])
