# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import patch

import pytest

from datus_db_core import DatusDbException, ErrorCode
from datus_oracle import OracleConfig, OracleConnector


def _make_connector(**config_kwargs) -> OracleConnector:
    defaults = {"username": "datus_test", "password": "pw", "service_name": "FREEPDB1"}
    defaults.update(config_kwargs)
    return OracleConnector(OracleConfig(**defaults))


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    config = OracleConfig(
        host="localhost",
        port=1521,
        username="datus_test",
        password="pw",
        service_name="FREEPDB1",
        schema_name="sales",
    )
    connector = OracleConnector(config)

    assert connector.config == config
    assert connector.host == "localhost"
    assert connector.port == 1521
    assert connector.username == "datus_test"
    assert connector.dialect == "oracle"
    # Default schema is upper-cased (Oracle folds unquoted identifiers)
    assert connector.schema_name == "SALES"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    connector = OracleConnector(
        {
            "host": "192.168.1.100",
            "username": "admin",
            "password": "secret",
            "service_name": "ORCLPDB1",
        }
    )

    assert connector.host == "192.168.1.100"
    assert connector.username == "admin"
    # Default schema falls back to the connecting user's schema
    assert connector.schema_name == "ADMIN"
    assert "service_name=ORCLPDB1" in connector.connection_string


def test_connector_initialization_invalid_type():
    with pytest.raises(TypeError, match="config must be OracleConfig or dict"):
        OracleConnector("invalid_config")


@pytest.mark.acceptance
def test_quote_identifier_upper_cases():
    connector = _make_connector()
    assert connector.quote_identifier("orders") == '"ORDERS"'
    assert connector.quote_identifier("order") == '"ORDER"'


@pytest.mark.acceptance
def test_full_name_schema_qualified():
    connector = _make_connector(schema_name="SALES")
    assert connector.full_name(table_name="orders") == '"SALES"."ORDERS"'
    assert connector.full_name(schema_name="hr", table_name="employees") == '"HR"."EMPLOYEES"'


def test_identifier_unquoted():
    connector = _make_connector(schema_name="SALES")
    assert connector.identifier(table_name="ORDERS") == "SALES.ORDERS"


def test_get_databases_is_empty():
    """Oracle namespace is schema-only; the service/PDB is a connection target."""
    connector = _make_connector()
    assert connector.get_databases() == []


class TestSampleRowsSql:
    @pytest.mark.acceptance
    def test_uses_fetch_first(self):
        connector = _make_connector(schema_name="SALES")
        captured = []

        def fake_execute_pandas(sql, **kwargs):
            import pandas as pd

            captured.append(sql)
            return pd.DataFrame({"id": [1]})

        with (
            patch.object(connector, "_execute_pandas", side_effect=fake_execute_pandas),
            patch.object(connector, "connect"),
        ):
            result = connector.get_sample_rows(tables=["orders"], top_n=5)

        assert captured == ['SELECT * FROM "SALES"."ORDERS" FETCH FIRST 5 ROWS ONLY']
        assert "LIMIT" not in captured[0]
        assert len(result) == 1
        assert result[0]["identifier"] == "SALES.orders"


class TestMetadataSql:
    def _capture_metadata_sql(self, connector, table_type):
        captured = []

        def fake_execute_pandas(sql, **kwargs):
            import pandas as pd

            captured.append(sql)
            return pd.DataFrame({"owner": [], "table_name": []})

        with (
            patch.object(connector, "_execute_pandas", side_effect=fake_execute_pandas),
            patch.object(connector, "connect"),
        ):
            connector._get_metadata(table_type)
        return captured[0]

    @pytest.mark.acceptance
    def test_tables_query_uses_all_tables(self):
        connector = _make_connector(schema_name="sales")
        sql = self._capture_metadata_sql(connector, "table")
        assert "ALL_TABLES" in sql
        assert "OWNER = 'SALES'" in sql
        assert "DBA_" not in sql

    def test_views_query_uses_all_views(self):
        connector = _make_connector(schema_name="SALES")
        sql = self._capture_metadata_sql(connector, "view")
        assert "ALL_VIEWS" in sql

    def test_mv_query_uses_all_mviews(self):
        connector = _make_connector(schema_name="SALES")
        sql = self._capture_metadata_sql(connector, "mv")
        assert "ALL_MVIEWS" in sql

    def test_invalid_table_type_raises(self):
        connector = _make_connector()
        with pytest.raises(DatusDbException):
            connector._get_metadata("bogus")

    def test_schema_name_escapes_quotes(self):
        connector = _make_connector()
        captured = []

        def fake_execute_pandas(sql, **kwargs):
            import pandas as pd

            captured.append(sql)
            return pd.DataFrame({"owner": [], "table_name": []})

        with (
            patch.object(connector, "_execute_pandas", side_effect=fake_execute_pandas),
            patch.object(connector, "connect"),
        ):
            connector._get_metadata("table", schema_name="x'; DROP TABLE t --")
        assert "''" in captured[0]


class TestFormatColumnType:
    def test_number_with_precision_scale(self):
        assert OracleConnector._format_column_type("NUMBER", 22, 10, 2) == "NUMBER(10,2)"

    def test_number_with_precision_only(self):
        assert OracleConnector._format_column_type("NUMBER", 22, 19, 0) == "NUMBER(19)"

    def test_number_bare(self):
        assert OracleConnector._format_column_type("NUMBER", 22, None, None) == "NUMBER"

    def test_varchar2_with_length(self):
        assert OracleConnector._format_column_type("VARCHAR2", 255, None, None) == "VARCHAR2(255)"

    def test_timestamp_passthrough(self):
        assert OracleConnector._format_column_type("TIMESTAMP(6)", None, None, None) == "TIMESTAMP(6)"

    def test_clob_passthrough(self):
        assert OracleConnector._format_column_type("CLOB", 4000, None, None) == "CLOB"


class TestErrorMapping:
    def _map(self, message: str) -> DatusDbException:
        connector = _make_connector()
        return connector._handle_exception(Exception(message), sql="SELECT 1")

    @pytest.mark.acceptance
    def test_ora_00942_maps_to_table_not_exists(self):
        exc = self._map("ORA-00942: table or view does not exist")
        assert exc.code == ErrorCode.DB_TABLE_NOT_EXISTS

    @pytest.mark.acceptance
    def test_ora_00942_preserves_original_text(self):
        """Agent's transfer auto-create-table matches the driver's English text."""
        exc = self._map("ORA-00942: table or view does not exist")
        assert "does not exist" in str(exc)
        assert "table" in str(exc).lower()

    def test_ora_01017_maps_to_authentication_failed(self):
        exc = self._map("ORA-01017: invalid username/password; logon denied")
        assert exc.code == ErrorCode.DB_AUTHENTICATION_FAILED

    def test_ora_12154_maps_to_connection_failed(self):
        exc = self._map("ORA-12154: TNS:could not resolve the connect identifier specified")
        assert exc.code == ErrorCode.DB_CONNECTION_FAILED

    def test_ora_12541_maps_to_connection_failed(self):
        exc = self._map("ORA-12541: TNS:no listener")
        assert exc.code == ErrorCode.DB_CONNECTION_FAILED

    def test_ora_01031_maps_to_permission_denied(self):
        exc = self._map("ORA-01031: insufficient privileges")
        assert exc.code == ErrorCode.DB_PERMISSION_DENIED

    def test_ora_00001_maps_to_constraint_violation(self):
        exc = self._map("ORA-00001: unique constraint (SALES.PK_T) violated")
        assert exc.code == ErrorCode.DB_CONSTRAINT_VIOLATION

    def test_ora_01013_maps_to_timeout(self):
        exc = self._map("ORA-01013: user requested cancel of current operation")
        assert exc.code == ErrorCode.DB_EXECUTION_TIMEOUT

    def test_ora_00933_maps_to_syntax_error(self):
        exc = self._map("ORA-00933: SQL command not properly ended")
        assert exc.code == ErrorCode.DB_EXECUTION_SYNTAX_ERROR

    def test_unknown_ora_code_maps_to_execution_error(self):
        exc = self._map("ORA-04091: table is mutating")
        assert exc.code == ErrorCode.DB_EXECUTION_ERROR

    def test_all_mapped_errors_preserve_ora_text(self):
        messages = [
            "ORA-01017: invalid username/password; logon denied",
            "ORA-12154: TNS:could not resolve the connect identifier specified",
            "ORA-00942: table or view does not exist",
            "ORA-01031: insufficient privileges",
            "ORA-00001: unique constraint (SALES.PK_T) violated",
            "ORA-01013: user requested cancel of current operation",
            "ORA-00933: SQL command not properly ended",
            "ORA-04091: table is mutating",
        ]
        for message in messages:
            assert message in str(self._map(message))

    def test_non_ora_error_delegates_to_base(self):
        exc = self._map("connection refused by host")
        assert isinstance(exc, DatusDbException)

    def test_datus_exception_passthrough(self):
        connector = _make_connector()
        original = DatusDbException(ErrorCode.DB_EXECUTION_ERROR, message="already mapped")
        assert connector._handle_exception(original) is original


class TestRegistration:
    def test_register_populates_registry(self):
        import datus_oracle
        from datus_db_core import connector_registry
        from datus_db_core.registry import ConnectorRegistry
        from datus_oracle import OracleDialectOperations

        saved_connectors = ConnectorRegistry._connectors.copy()
        saved_metadata = ConnectorRegistry._metadata.copy()
        saved_capabilities = ConnectorRegistry._capabilities.copy()
        saved_uri_builders = ConnectorRegistry._uri_builders.copy()
        saved_context_resolvers = ConnectorRegistry._context_resolvers.copy()
        try:
            datus_oracle.register()

            assert connector_registry.is_registered("oracle")
            assert connector_registry.get_capabilities("oracle") == {"schema"}
            assert not connector_registry.support_catalog("oracle")
            assert not connector_registry.support_database("oracle")
            assert connector_registry.support_schema("oracle")
            assert connector_registry.get_parser_dialect("oracle") == "oracle"
            assert isinstance(connector_registry.get_dialect_operations("oracle"), OracleDialectOperations)
            notes = connector_registry.get_sql_generation_notes("oracle")
            assert callable(notes)
            assert "# Oracle SQL" in notes()
            assert "FETCH FIRST n ROWS ONLY" in notes()
            metadata = connector_registry.get_metadata("oracle")
            assert metadata.config_class is OracleConfig
            assert "service_name" in metadata.get_config_fields()
        finally:
            ConnectorRegistry._connectors = saved_connectors
            ConnectorRegistry._metadata = saved_metadata
            ConnectorRegistry._capabilities = saved_capabilities
            ConnectorRegistry._uri_builders = saved_uri_builders
            ConnectorRegistry._context_resolvers = saved_context_resolvers
