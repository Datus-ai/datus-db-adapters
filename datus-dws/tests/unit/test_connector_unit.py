# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Offline tests for DWSConnector.

The base class creates its engine lazily, so a connector can be constructed and
its pure logic exercised without reaching a cluster.
"""

import os
from urllib.parse import parse_qs, urlparse

import pytest

from datus_dws import DWSConfig, DWSConnector

# Verbatim pg_get_tabledef() output from a live DWS 9.1.0 storage-decoupled
# cluster; the cluster-specific clauses are what migration has to remove.
COLUMN_TABLE_DDL = """SET search_path = datus_dws_probe;
CREATE  TABLE col_hash_compress (
\tid integer,
\tname character varying(64),
\tamt numeric(10,2)
)
WITH (orientation=column, compression=middle, colversion=3.0, enable_delta=false)
TABLESPACE cu_obs_tbs
DISTRIBUTE BY HASH(id)
TO GROUP v3_logical;"""

REPLICATION_TABLE_DDL = """SET search_path = datus_dws_probe;
CREATE  TABLE row_replication (
\tid integer,
\tname character varying(64)
)
WITH (orientation=row, compression=no)
DISTRIBUTE BY REPLICATION
TO GROUP v3_logical;"""


def _config(**overrides):
    values = {
        "host": "example.dws.myhuaweicloud.com",
        "username": "dbadmin",
        "password": "secret-value",
        "database": "gaussdb",
    }
    values.update(overrides)
    return DWSConfig(**values)


def _connector(**overrides):
    return DWSConnector(_config(**overrides))


# ==================== construction ====================


def test_connector_accepts_dict_config():
    connector = DWSConnector(
        {
            "host": "example.dws.myhuaweicloud.com",
            "username": "dbadmin",
            "password": "x",
            "database": "gaussdb",
        }
    )

    assert isinstance(connector.config, DWSConfig)


def test_connector_rejects_foreign_config_type():
    with pytest.raises(TypeError, match="must be DWSConfig or dict"):
        DWSConnector(object())


def test_dialect_is_dws_not_postgresql():
    # Datus routes capabilities, parsing and prompts off this value, so it must
    # not stay at the base class's "postgresql".
    assert _connector().dialect == "dws"


# ==================== connection string ====================


def test_connection_string_uses_psycopg2_and_carries_sslmode():
    url = _connector(sslmode="verify-ca")._build_connection_string("gaussdb")

    assert url.startswith("postgresql+psycopg2://")
    assert parse_qs(urlparse(url).query)["sslmode"] == ["verify-ca"]


def test_connection_string_includes_sslrootcert_path():
    url = _connector(sslmode="verify-ca", sslrootcert="/etc/ssl/cacert.pem")._build_connection_string("gaussdb")

    assert parse_qs(urlparse(url).query)["sslrootcert"] == ["/etc/ssl/cacert.pem"]


def test_connection_string_omits_sslrootcert_when_unset():
    url = _connector()._build_connection_string("gaussdb")

    assert "sslrootcert" not in url


def test_inline_pem_is_materialised_to_a_readable_file():
    pem = "-----BEGIN CERTIFICATE-----\nMIIBtjCCAVug\n-----END CERTIFICATE-----\n"
    url = _connector(sslmode="verify-ca", sslrootcert=pem)._build_connection_string("gaussdb")

    path = parse_qs(urlparse(url).query)["sslrootcert"][0]
    # psycopg2 takes a filename, so the certificate body must not reach the URL.
    assert "BEGIN CERTIFICATE" not in url
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == pem


def test_connection_string_escapes_credentials():
    url = _connector(username="ad/min", password="p@ss/word")._build_connection_string("gaussdb")

    assert "ad%2Fmin" in url
    assert "p%40ss%2Fword" in url
    # A raw '/' in the password would terminate the authority section early.
    assert urlparse(url).hostname == "example.dws.myhuaweicloud.com"


def test_connection_string_brackets_ipv6_host():
    url = _connector(host="2001:db8::1")._build_connection_string("gaussdb")

    parsed = urlparse(url)
    assert parsed.hostname == "2001:db8::1"
    assert parsed.port == 8000


def test_connection_string_targets_the_requested_database():
    url = _connector()._build_connection_string("reporting")

    assert urlparse(url).path == "/reporting"


# ==================== system schemas ====================


@pytest.mark.parametrize(
    "schema",
    [
        # Verified by listing pg_namespace in full on a live DWS 9.1.0 cluster.
        "cstore",
        "dbms_job",
        "dbms_lob",
        "dbms_om",
        "dbms_output",
        "dbms_random",
        "dbms_sql",
        "gs_logical_cluster",
        "pg_recyclebin",
        "scheduler",
        "sys",
        "utl_file",
        "utl_raw",
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "pg_temp_3",
        "pg_toast_temp_3",
        "dbe_perf",
    ],
)
def test_dws_system_schemas_are_filtered(schema):
    assert _connector()._is_sys_schema(schema) is True


@pytest.mark.parametrize("schema", ["public", "analytics", "dbadmin", "sales_dwd", "utler", "dbms"])
def test_user_schemas_are_not_filtered(schema):
    # The login role's own schema is where an ordinary user's tables land, and
    # a name that merely starts like a package prefix is not a system schema.
    assert _connector()._is_sys_schema(schema) is False


# ==================== cluster-specific DDL clauses ====================


def test_strip_removes_tablespace_and_node_group():
    stripped = DWSConnector.strip_cluster_specific_clauses(COLUMN_TABLE_DDL)

    assert "TABLESPACE" not in stripped
    assert "TO GROUP" not in stripped


def test_strip_keeps_storage_and_distribution_semantics():
    stripped = DWSConnector.strip_cluster_specific_clauses(COLUMN_TABLE_DDL)

    assert "orientation=column" in stripped
    assert "compression=middle" in stripped
    assert "DISTRIBUTE BY HASH(id)" in stripped
    assert "numeric(10,2)" in stripped


def test_strip_preserves_the_statement_terminator():
    stripped = DWSConnector.strip_cluster_specific_clauses(COLUMN_TABLE_DDL)

    # 'TO GROUP v3_logical;' must lose the clause but keep the semicolon,
    # otherwise the emitted DDL is not a complete statement.
    assert stripped.rstrip().endswith(";")
    assert stripped.rstrip().endswith("DISTRIBUTE BY HASH(id);")


def test_strip_handles_replication_tables_without_tablespace():
    stripped = DWSConnector.strip_cluster_specific_clauses(REPLICATION_TABLE_DDL)

    assert "TO GROUP" not in stripped
    assert stripped.rstrip().endswith("DISTRIBUTE BY REPLICATION;")


def test_strip_removes_quoted_names_containing_spaces():
    """A quoted identifier may contain spaces, so the clause cannot end at the
    first whitespace — stopping there leaves a dangling fragment behind and the
    emitted DDL no longer parses."""
    ddl = 'CREATE TABLE t (id integer)\nTABLESPACE "obs tbs"\nDISTRIBUTE BY HASH(id)\nTO GROUP "node group";'

    stripped = DWSConnector.strip_cluster_specific_clauses(ddl)

    assert stripped == "CREATE TABLE t (id integer)\nDISTRIBUTE BY HASH(id);"
    assert '"' not in stripped
    assert "tbs" not in stripped
    assert "group" not in stripped.lower()


def test_strip_removes_quoted_names_with_doubled_quote_escape():
    ddl = 'CREATE TABLE t (id integer)\nTO GROUP "we""ird";'

    stripped = DWSConnector.strip_cluster_specific_clauses(ddl)

    assert stripped == "CREATE TABLE t (id integer);"


def test_strip_is_a_noop_for_portable_ddl():
    portable = "CREATE TABLE t (id integer)\nDISTRIBUTE BY HASH(id);"

    assert DWSConnector.strip_cluster_specific_clauses(portable) == portable


def test_strip_tolerates_empty_input():
    assert DWSConnector.strip_cluster_specific_clauses("") == ""
