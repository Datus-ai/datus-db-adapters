# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Server-encoding coverage: GBK databases with Chinese identifiers and data.

Chinese GaussDB deployments frequently run ``server_encoding = GBK`` rather
than UTF8. The drivers always negotiate ``client_encoding = UTF8`` and the
server transcodes, so Chinese table/column names, Chinese row data, and
Chinese predicates must round-trip losslessly regardless of the on-disk
encoding. A GBK database is created on demand (CREATEDB required; the module
skips gracefully without it).
"""

import os
import sys

import pytest

from datus_gaussdb import GaussDBConfig, GaussDBConnector

pytestmark = pytest.mark.integration

GBK_DB = "datus_enc_gbk"


@pytest.fixture(params=["pg8000", "gaussdb"])
def gbk_connector(request, config: GaussDBConfig):
    """Both client drivers, so the GBK behavior of each is asserted: pg8000
    must pin client_encoding=UTF8, the official driver decodes GBK natively.
    The official driver needs the GaussDB libpq (linux-only)."""
    driver = request.param
    if driver == "gaussdb" and sys.platform != "linux" and os.getenv("GAUSSDB_FORCE_INTEGRATION") != "1":
        pytest.skip("the official gaussdb driver needs the GaussDB libpq, which has no build here")

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
        try:
            cur.execute(
                f"CREATE DATABASE {GBK_DB} WITH ENCODING 'GBK' "
                "LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0 DBCOMPATIBILITY 'PG'"
            )
        except Exception as e:  # noqa: BLE001 - classify for skip-vs-fail
            message = str(e)
            if "already exists" not in message:
                # Only the expected insufficient-privilege case skips; any
                # other failure must surface, not hide as a skip.
                if "permission denied" in message.lower():
                    pytest.skip(f"login lacks CREATEDB: {e}")
                raise
    finally:
        conn.close()

    connector = GaussDBConnector(config.model_copy(update={"database": GBK_DB, "driver": driver}))
    # Ordinary users have no CREATE on public even in a database they own
    # (openGauss default); a dedicated schema owned by the test user avoids
    # depending on superuser grants.
    connector.execute({"sql_query": "CREATE SCHEMA IF NOT EXISTS enc_test"}, result_format="list")
    yield connector
    connector.close()


def test_gbk_database_client_encoding(gbk_connector):
    """pg8000 must pin client_encoding=UTF8 (it has no GBK codec mapping and
    would silently mis-decode); the official psycopg-family driver carries a
    full encoding map and decodes a GBK session correctly as-is."""
    result = gbk_connector.execute(
        {
            "sql_query": (
                "SELECT current_setting('server_encoding') AS server_enc, "
                "current_setting('client_encoding') AS client_enc"
            )
        },
        result_format="list",
    )
    row = list(result.sql_return[0].values())
    assert row[0] == "GBK"
    if gbk_connector.config.driver == "pg8000":
        assert row[1].upper() in ("UTF8", "UTF-8")


def test_gbk_chinese_identifiers_and_data_round_trip(gbk_connector):
    c = gbk_connector
    c.execute({"sql_query": 'DROP TABLE IF EXISTS "enc_test"."订单表"'}, result_format="list")
    c.execute(
        {"sql_query": 'CREATE TABLE "enc_test"."订单表" ("订单号" INT, "客户名" VARCHAR(64), "备注" TEXT)'},
        result_format="list",
    )
    c.execute(
        {
            "sql_query": (
                'INSERT INTO "enc_test"."订单表" VALUES '
                "(1, '张三', '加急——今天发货（华东仓）'), "
                "(2, '李四·全角，测试', NULL)"
            )
        },
        result_format="list",
    )

    # Data round-trip, including a Chinese predicate.
    result = c.execute(
        {"sql_query": 'SELECT "客户名", "备注" FROM "enc_test"."订单表" WHERE "客户名" = \'张三\''},
        result_format="list",
    )
    assert result.sql_return == [{"客户名": "张三", "备注": "加急——今天发货（华东仓）"}]

    # Chinese LIKE and length semantics survive transcoding.
    result = c.execute(
        {"sql_query": 'SELECT count(*) AS n FROM "enc_test"."订单表" WHERE "客户名" LIKE \'%四%\''},
        result_format="list",
    )
    assert list(result.sql_return[0].values()) == [1]
    result = c.execute(
        {"sql_query": "SELECT length('华东仓') AS n"},
        result_format="list",
    )
    assert list(result.sql_return[0].values()) == [3]

    # Metadata reflection sees the Chinese table and columns.
    tables = c.get_tables(schema_name="enc_test")
    assert any(t == "订单表" or t.endswith(".订单表") for t in tables), tables
    ddl_entries = c.get_tables_with_ddl(schema_name="enc_test")
    entry = next(e for e in ddl_entries if e["table_name"] == "订单表")
    for column in ("订单号", "客户名", "备注"):
        assert column in entry["definition"], entry["definition"]

    c.execute({"sql_query": 'DROP TABLE "enc_test"."订单表"'}, result_format="list")
