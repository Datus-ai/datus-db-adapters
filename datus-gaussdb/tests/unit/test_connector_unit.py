# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Fully mocked GaussDBConnector tests.

``SQLAlchemyConnector.__init__`` is patched out so nothing ever creates an
engine or imports a database driver: ``GaussDBConnector.__init__`` chains
through ``PostgreSQLConnector.__init__``, and everything those two set on the
instance (host/port/credentials, ``_default_database``, dialect, connection
string) still runs.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from datus_gaussdb import GaussDBConfig, GaussDBConnector
from datus_gaussdb.connector import DbTraits
from datus_postgresql import PostgreSQLConnector

_SA_INIT = "datus_sqlalchemy.SQLAlchemyConnector.__init__"


def _make_connector(**overrides) -> GaussDBConnector:
    """Build a connector whose SQLAlchemy layer is inert."""
    kwargs = {
        "username": "datus",
        "host": "gauss.internal",
        "port": 25434,
        "database": "postgres",
        "driver": "gaussdb",
    }
    kwargs.update(overrides)
    with patch(_SA_INIT, return_value=None):
        return GaussDBConnector(GaussDBConfig(**kwargs))


def _conn_returning(conn) -> MagicMock:
    """Replacement for ``_conn`` that yields ``conn`` on every use."""

    @contextmanager
    def _cm(**_kwargs):
        yield conn

    return MagicMock(side_effect=lambda **kwargs: _cm(**kwargs))


# ==================== Initialization ====================


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Init keeps the GaussDB config and the fields the parent derives from it."""
    config = GaussDBConfig(
        host="gauss.internal",
        port=25434,
        username="datus",
        password="Datus@123",
        database="analyticsdb",
        schema="analytics",
    )

    with patch(_SA_INIT, return_value=None):
        connector = GaussDBConnector(config)

    assert connector.config is config
    assert connector.host == "gauss.internal"
    assert connector.port == 25434
    assert connector.username == "datus"
    assert connector.password == "Datus@123"
    assert connector.database_name == "analyticsdb"
    assert connector.schema_name == "analytics"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Dict config is coerced into GaussDBConfig."""
    with patch(_SA_INIT, return_value=None):
        connector = GaussDBConnector(
            {
                "host": "gauss.internal",
                "port": 25434,
                "username": "datus",
                "password": "Datus@123",
                "database": "postgres",
                "driver": "psycopg2",
            }
        )

    assert isinstance(connector.config, GaussDBConfig)
    assert connector.config.driver == "psycopg2"
    assert connector.database_name == "postgres"


@pytest.mark.acceptance
def test_connector_initialization_invalid_type():
    """A non-config, non-dict argument is rejected before any connection work."""
    with pytest.raises(TypeError, match="config must be GaussDBConfig or dict"):
        GaussDBConnector("gaussdb://localhost")


@pytest.mark.acceptance
def test_connector_dialect_is_gaussdb():
    """The parent fixes dialect='postgresql'; GaussDB must re-point it."""
    connector = _make_connector()

    assert connector.dialect == "gaussdb"


@pytest.mark.acceptance
def test_connector_database_defaults_to_postgres():
    """Without an explicit database the parent's 'postgres' default applies."""
    connector = _make_connector(database=None)

    assert connector.database_name == "postgres"
    assert "/postgres?" in connector.connection_string


# ==================== Connection String ====================


@pytest.mark.acceptance
def test_connection_string_uses_gaussdb_driver_by_default():
    """The default driver routes through the gaussdb+psycopg SQLAlchemy dialect."""
    connector = _make_connector(password="Datus@123", database="analyticsdb")

    assert connector.connection_string == (
        "gaussdb+psycopg://datus:Datus%40123@gauss.internal:25434/analyticsdb?sslmode=prefer"
    )


@pytest.mark.acceptance
def test_connection_string_uses_gaussdb_dialect_for_psycopg2():
    """The psycopg2 escape hatch keeps GaussDB-specific dialect behavior."""
    connector = _make_connector(driver="psycopg2", password="pass", database="analyticsdb")

    assert connector.connection_string == (
        "gaussdb+psycopg2://datus:pass@gauss.internal:25434/analyticsdb?sslmode=prefer"
    )


@pytest.mark.acceptance
def test_connection_string_uses_pg8000_dialect():
    """The pure-Python pg8000 path routes through gaussdb+pg8000."""
    connector = _make_connector(driver="pg8000", password="pass", database="analyticsdb")

    assert connector.connection_string == (
        "gaussdb+pg8000://datus:pass@gauss.internal:25434/analyticsdb?sslmode=prefer"
    )


@pytest.mark.acceptance
def test_connection_string_carries_sslrootcert():
    """verify-ca/verify-full need the CA path forwarded to the dialect."""
    connector = _make_connector(
        driver="pg8000",
        password="pass",
        sslmode="verify-full",
        sslrootcert="/etc/ssl/gauss-ca.pem",
    )

    assert "sslmode=verify-full" in connector.connection_string
    assert "sslrootcert=%2Fetc%2Fssl%2Fgauss-ca.pem" in connector.connection_string


@pytest.mark.acceptance
def test_connection_string_encodes_special_characters():
    """Credentials with URL-significant characters are percent-encoded."""
    connector = _make_connector(username="user@corp", password="p@ss!w0rd#$%")

    assert "user%40corp:p%40ss%21w0rd%23%24%25@gauss.internal:25434" in connector.connection_string


@pytest.mark.acceptance
def test_connection_string_honors_sslmode():
    """sslmode is carried into the connection string."""
    connector = _make_connector(sslmode="require")

    assert connector.connection_string.endswith("?sslmode=require")


@pytest.mark.acceptance
def test_build_connection_string_targets_requested_database():
    """Per-database engines are built from the requested database name."""
    connector = _make_connector()

    assert connector._build_connection_string("otherdb").endswith("/otherdb?sslmode=prefer")


# ==================== System Resources ====================


@pytest.mark.acceptance
def test_sys_schemas_extends_postgresql():
    """GaussDB adds its own internal schemas on top of the PostgreSQL set."""
    connector = _make_connector()
    pg_schemas = PostgreSQLConnector.__new__(PostgreSQLConnector)._sys_schemas()

    sys_schemas = connector._sys_schemas()

    assert pg_schemas.issubset(sys_schemas)
    assert "pg_catalog" in sys_schemas
    assert {
        "dbe_perf",
        "db4ai",
        "snapshot",
        "cstore",
        "blockchain",
        "sqladvisor",
    }.issubset(sys_schemas)


@pytest.mark.acceptance
def test_sys_databases_matches_postgresql():
    """GaussDB has no extra system databases beyond the PostgreSQL templates."""
    connector = _make_connector()

    assert connector._sys_databases() == {"template0", "template1"}


@pytest.mark.acceptance
def test_get_schemas_uses_pg_namespace_and_filters_internal_prefixes():
    """Schema discovery uses pg_namespace and hides system and temporary schemas."""
    connector = _make_connector()
    result = MagicMock()
    result.__getitem__.return_value.tolist.return_value = [
        "public",
        "pg_catalog",
        "dbe_perf",
        "pg_temp_3",
    ]
    connector._execute_pandas = MagicMock(return_value=result)

    schemas = connector.get_schemas(database_name="analytics")

    assert schemas == ["public"]
    sql = connector._execute_pandas.call_args.args[0]
    assert "pg_namespace" in sql
    connector._execute_pandas.assert_called_once_with(sql, database_name="analytics")


# ==================== Feature Probing ====================


@pytest.mark.acceptance
def test_get_traits_probes_catalog_and_caches():
    """Traits are probed once per database and served from cache afterwards."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=["B", 3, 0])

    first = connector._get_traits()

    assert first.compat_mode == "B"
    assert first.is_distributed is True
    assert first.has_matviews is True
    assert connector._probe_scalar.call_count == 3

    second = connector._get_traits()

    assert second is first
    assert connector._probe_scalar.call_count == 3


@pytest.mark.acceptance
def test_each_probe_uses_its_own_connection():
    """A failed probe aborts its transaction, so probes must not share a connection."""
    connector = _make_connector()
    connector._conn = _conn_returning(MagicMock())
    connector._exec_scalar = MagicMock(side_effect=["A", 1, 0])

    connector._get_traits()

    assert connector._conn.call_count == 3
    assert connector._exec_scalar.call_count == 3


@pytest.mark.acceptance
def test_get_traits_single_node_is_not_distributed():
    """pgxc_node exists on centralized openGauss too; only >1 node means distributed."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=["A", 1, 0])

    assert connector._get_traits().is_distributed is False


@pytest.mark.acceptance
def test_get_traits_caches_per_database():
    """Different databases get their own probe result."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=["A", 1, 0, "B", 5, 0])

    default_traits = connector._get_traits()
    other_traits = connector._get_traits("otherdb")

    assert default_traits.compat_mode == "A"
    assert default_traits.is_distributed is False
    assert other_traits.compat_mode == "B"
    assert other_traits.is_distributed is True
    assert set(connector._traits_cache) == {"postgres", "otherdb"}


@pytest.mark.acceptance
def test_get_traits_normalizes_compat_mode():
    """The catalog value is upper-cased and trimmed before use."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=["  a  ", 1, 0])

    assert connector._get_traits().compat_mode == "A"


@pytest.mark.acceptance
def test_get_traits_flags_missing_matviews():
    """A server without pg_matviews is recorded as lacking materialized views."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=["A", 1, Exception("relation pg_matviews does not exist")])

    traits = connector._get_traits()

    assert traits.has_matviews is False
    assert traits.compat_mode == "A"


@pytest.mark.acceptance
def test_get_traits_survives_a_failed_compat_probe():
    """One failing probe must not skip the probes after it."""
    connector = _make_connector()
    connector._probe_scalar = MagicMock(side_effect=[Exception("permission denied for pg_database"), 5, 0])

    traits = connector._get_traits()

    assert traits.compat_mode == "A"
    assert traits.is_distributed is True
    assert traits.has_matviews is True


@pytest.mark.acceptance
def test_get_traits_degrades_when_unreachable():
    """An unreachable database yields usable defaults instead of an exception."""
    connector = _make_connector()
    connector._conn = MagicMock(side_effect=Exception("connection refused"))

    traits = connector._get_traits()

    assert traits.compat_mode == "A"
    assert traits.is_distributed is False


# ==================== Materialized Views ====================


@pytest.mark.acceptance
def test_get_materialized_views_short_circuits_without_matviews():
    """Servers without pg_matviews return [] instead of raising in the parent."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(has_matviews=False))

    with patch.object(PostgreSQLConnector, "get_materialized_views") as mock_super:
        result = connector.get_materialized_views(schema_name="public")

    assert result == []
    mock_super.assert_not_called()


@pytest.mark.acceptance
def test_get_materialized_views_delegates_when_supported():
    """With pg_matviews present the parent implementation is used unchanged."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(has_matviews=True))

    with patch.object(PostgreSQLConnector, "get_materialized_views", return_value=["public.mv1"]) as mock_super:
        result = connector.get_materialized_views("", "analyticsdb", "public")

    assert result == ["public.mv1"]
    mock_super.assert_called_once_with("", "analyticsdb", "public")


# ==================== Distribution Clause ====================


@pytest.mark.acceptance
def test_distribution_clause_hash():
    """A hash-distributed table reports its distribution columns."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("H", "1 3")
    connector._conn = _conn_returning(conn)
    connector._get_attribute_names = MagicMock(return_value=["o_orderkey", "o_custkey"])

    clause = connector._get_distribution_clause("public", "orders")

    assert clause == "DISTRIBUTE BY HASH (o_orderkey, o_custkey)"


@pytest.mark.acceptance
def test_distribution_clause_replication():
    """Locator type 'R' maps to REPLICATION."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("R", None)
    connector._conn = _conn_returning(conn)

    assert connector._get_distribution_clause("public", "nation") == "DISTRIBUTE BY REPLICATION"


@pytest.mark.acceptance
def test_distribution_clause_roundrobin():
    """Locator type 'N' maps to ROUNDROBIN."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("N", None)
    connector._conn = _conn_returning(conn)

    assert connector._get_distribution_clause("public", "staging") == "DISTRIBUTE BY ROUNDROBIN"


@pytest.mark.acceptance
def test_distribution_clause_empty_when_not_in_catalog():
    """A table absent from pgxc_class has no distribution clause."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    connector._conn = _conn_returning(conn)

    assert connector._get_distribution_clause("public", "orders") == ""


@pytest.mark.acceptance
def test_distribution_clause_empty_on_error():
    """Catalog errors degrade to no clause rather than failing DDL retrieval."""
    connector = _make_connector()
    connector._conn = MagicMock(side_effect=Exception("relation pgxc_class does not exist"))

    assert connector._get_distribution_clause("public", "orders") == ""


@pytest.mark.acceptance
def test_distribution_clause_binds_identifiers_as_parameters():
    """Schema and table names are bound, never interpolated into the SQL text."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("R", None)
    connector._conn = _conn_returning(conn)

    connector._get_distribution_clause("it's", "tab'le")

    params = conn.execute.call_args[0][1]
    assert params == {"table_name": "tab'le", "schema_name": "it's"}


@pytest.mark.acceptance
def test_attribute_names_map_attnums_in_order():
    """Distribution columns follow the attnum order recorded in the catalog."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [
        (1, "id"),
        (2, "region"),
        (3, "name"),
    ]
    connector._conn = _conn_returning(conn)

    assert connector._get_attribute_names("public", "orders", "3 1") == ["name", "id"]
    assert connector._get_attribute_names("public", "orders", [2]) == ["region"]
    assert connector._get_attribute_names("public", "orders", 1) == ["id"]


@pytest.mark.acceptance
def test_attribute_names_drop_everything_when_one_attnum_is_unresolved():
    """A partial key would emit a DISTRIBUTE BY with the wrong columns."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(1, "id")]
    connector._conn = _conn_returning(conn)

    assert connector._get_attribute_names("public", "orders", "1 7") == []


@pytest.mark.acceptance
def test_distribution_clause_omitted_when_columns_are_unresolved():
    """An unresolvable hash key yields no clause rather than a truncated one."""
    connector = _make_connector()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("H", "1 7")
    connector._conn = _conn_returning(conn)
    connector._get_attribute_names = MagicMock(return_value=[])

    assert connector._get_distribution_clause("public", "orders") == ""


# ==================== Migration Capabilities ====================


@pytest.mark.acceptance
def test_describe_migration_capabilities_flags_a_mode_empty_string():
    """Migration callers must be warned that '' does not round-trip in A mode."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits())

    capabilities = connector.describe_migration_capabilities()

    assert capabilities["dialect_family"] == "gaussdb"
    assert capabilities["supported"] is True
    assert len(capabilities["notes"]) == 1
    assert "empty strings are stored as NULL" in capabilities["notes"][0]


@pytest.mark.acceptance
def test_describe_migration_capabilities_adds_distribution_note():
    """Distributed deployments get the extra distribution-key warning."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(is_distributed=True))

    notes = connector.describe_migration_capabilities()["notes"]

    assert len(notes) == 2
    assert "DISTRIBUTE BY HASH" in notes[1]
    assert "cannot be UPDATEd" in notes[1]


@pytest.mark.acceptance
@pytest.mark.parametrize("mode", ["B", "PG"])
def test_describe_migration_capabilities_omits_a_mode_note_for_other_modes(mode):
    """Only 'A' mode collapses empty strings, so B/PG must not carry that note."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(compat_mode=mode))

    notes = connector.describe_migration_capabilities()["notes"]

    assert notes == []


# ==================== DDL Augmentation ====================


@pytest.mark.acceptance
def test_get_ddl_appends_distribution_on_distributed_deployment():
    """Table DDL carries the reconstructed DISTRIBUTE BY clause."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(is_distributed=True))
    connector._get_distribution_clause = MagicMock(return_value="DISTRIBUTE BY HASH (id)")

    with patch.object(
        PostgreSQLConnector,
        "_get_ddl",
        return_value='CREATE TABLE "public"."t" (\n    "id" integer\n);',
    ):
        ddl = connector._get_ddl("public", "t", "TABLE")

    assert ddl.endswith("DISTRIBUTE BY HASH (id);")
    assert ddl.count(";") == 1


@pytest.mark.acceptance
def test_get_ddl_untouched_on_centralized_deployment():
    """openGauss (non-distributed) DDL is returned as-is."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(is_distributed=False))
    connector._get_distribution_clause = MagicMock()

    with patch.object(
        PostgreSQLConnector,
        "_get_ddl",
        return_value='CREATE TABLE "public"."t" ("id" integer);',
    ):
        ddl = connector._get_ddl("public", "t", "TABLE")

    assert ddl == 'CREATE TABLE "public"."t" ("id" integer);'
    connector._get_distribution_clause.assert_not_called()


@pytest.mark.acceptance
def test_get_ddl_skips_distribution_for_views():
    """Only tables carry a distribution policy."""
    connector = _make_connector()
    connector._get_traits = MagicMock(return_value=DbTraits(is_distributed=True))
    connector._get_distribution_clause = MagicMock()

    with patch.object(
        PostgreSQLConnector,
        "_get_ddl",
        return_value='CREATE VIEW "public"."v" AS\nSELECT 1',
    ):
        ddl = connector._get_ddl("public", "v", "VIEW")

    assert "DISTRIBUTE" not in ddl
    connector._get_distribution_clause.assert_not_called()
