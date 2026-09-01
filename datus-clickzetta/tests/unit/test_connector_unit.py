# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Mocked unit tests for ClickZettaConnector.

Every test patches ``datus_clickzetta.connector.Session`` and asserts on the SQL the connector
emits, so the module needs neither ClickZetta credentials nor network access.
"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pyarrow as pa
import pytest

from datus_clickzetta.connector import ClickZettaConnector
from datus_db_core import DatusDbException


@pytest.fixture
def clickzetta_test_config():
    """Connector kwargs fixed in-process; unit tests must not read CLICKZETTA_* env vars."""
    return {
        "service": "test-service.clickzetta.com",
        "username": "testuser",
        "password": "testpass",
        "instance": "test_instance",
        "workspace": "test_workspace",
        "schema": "PUBLIC",
        "vcluster": "DEFAULT_AP",
    }


def _bind_session(mock_session_class):
    """Wire ``Session.builder.configs(...).create()`` to a fresh session mock."""
    session = MagicMock()
    mock_session_class.builder.configs.return_value.create.return_value = session
    return session


def _executed_sql(session):
    """SQL text of every ``session.sql(...)`` call, including the USE statements from connect()."""
    return [call.args[0] for call in session.sql.call_args_list]


class TestConnectorInitialization:
    """Constructor argument handling and dependency checks."""

    def test_connector_creation(self, clickzetta_test_config):
        """Constructor maps its keyword arguments onto the connector's public attributes."""
        with patch("datus_clickzetta.connector.Session"):
            connector = ClickZettaConnector(**clickzetta_test_config)

            assert connector.service == "test-service.clickzetta.com"
            assert connector.user == "testuser"
            assert connector.password == "testpass"
            assert connector.instance == "test_instance"
            assert connector.database_name == "test_workspace"
            assert connector.schema_name == "PUBLIC"
            assert connector.vcluster == "DEFAULT_AP"

            connector.close()

    def test_connector_with_missing_dependency(self, clickzetta_test_config):
        """A missing zettapark install fails at construction with an actionable message."""
        with patch("datus_clickzetta.connector.Session", None):
            with pytest.raises(DatusDbException, match="ClickZetta connector requires the packages"):
                ClickZettaConnector(**clickzetta_test_config)

    @pytest.mark.parametrize("field", ["service", "username", "password", "instance", "workspace"])
    def test_connector_missing_required_fields(self, clickzetta_test_config, field):
        """An empty required field is rejected and the error names the offending field."""
        clickzetta_test_config[field] = ""

        with patch("datus_clickzetta.connector.Session"):
            with pytest.raises(DatusDbException, match=f"Missing ClickZetta connection fields: {field}"):
                ClickZettaConnector(**clickzetta_test_config)


class TestConnectorOperations:
    """Session lifecycle, query execution, and context switching."""

    @patch("datus_clickzetta.connector.Session")
    def test_connection_management(self, mock_session_class, clickzetta_test_config):
        """connect() builds the session once and applies the configured schema and vcluster."""
        session = _bind_session(mock_session_class)

        connector = ClickZettaConnector(**clickzetta_test_config)
        connector.connect()

        mock_session_class.builder.configs.assert_called_once_with(connector._connection_config)
        mock_session_class.builder.configs.return_value.create.assert_called_once()
        assert _executed_sql(session) == ["USE SCHEMA `PUBLIC`", "USE VCLUSTER `DEFAULT_AP`"]

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_query_execution(self, mock_session_class, clickzetta_test_config):
        """execute_query forwards the SQL verbatim and reports the row count of the result frame."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})

        connector = ClickZettaConnector(**clickzetta_test_config)
        result = connector.execute_query("SELECT * FROM test_table")

        session.sql.assert_called_with("SELECT * FROM test_table")
        assert result.success
        assert result.row_count == 3

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_context_switching(self, mock_session_class, clickzetta_test_config):
        """Schema switching is issued as USE SCHEMA; workspace switching is refused by design."""
        session = _bind_session(mock_session_class)

        connector = ClickZettaConnector(**clickzetta_test_config)

        connector.do_switch_context(schema_name="NEW_SCHEMA")
        assert "USE SCHEMA `NEW_SCHEMA`" in _executed_sql(session)
        assert connector.schema_name == "NEW_SCHEMA"

        with pytest.raises(
            DatusDbException,
            match="Workspace switching from 'test_workspace' to 'other_workspace' is not supported",
        ):
            connector.do_switch_context(database_name="other_workspace")

        connector.close()


class TestMetadataOperations:
    """Metadata listings built on information_schema queries."""

    @patch("datus_clickzetta.connector.Session")
    def test_get_tables(self, mock_session_class, clickzetta_test_config):
        """get_tables queries the workspace information_schema and keeps only table-like types."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame(
            {
                "table_name": ["table1", "table2", "a_view"],
                "table_type": ["MANAGED_TABLE", "BASE TABLE", "VIEW"],
            }
        )

        connector = ClickZettaConnector(**clickzetta_test_config)
        tables = connector.get_tables(database_name="test_workspace", schema_name="test_schema")

        assert tables == ["table1", "table2"]
        assert (
            "SELECT table_name, table_type FROM `test_workspace`.information_schema.tables "
            "WHERE upper(table_schema) = 'TEST_SCHEMA'" in _executed_sql(session)
        )

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_get_views(self, mock_session_class, clickzetta_test_config):
        """get_views keeps VIEW and DYNAMIC_TABLE rows and drops the managed tables."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame(
            {
                "table_name": ["view1", "view2", "a_table"],
                "table_type": ["VIEW", "DYNAMIC_TABLE", "MANAGED_TABLE"],
            }
        )

        connector = ClickZettaConnector(**clickzetta_test_config)
        views = connector.get_views(database_name="test_workspace", schema_name="test_schema")

        assert views == ["view1", "view2"]
        assert (
            "SELECT table_name, table_type FROM `test_workspace`.information_schema.tables "
            "WHERE upper(table_schema) = 'TEST_SCHEMA'" in _executed_sql(session)
        )

        connector.close()


class TestVolumeListing:
    """Volume/stage file listing."""

    @patch("datus_clickzetta.connector.Session")
    def test_list_volume_files(self, mock_session_class, clickzetta_test_config):
        """User volumes are listed with a SUBDIRECTORY clause and filtered down to the suffixes."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame(
            {
                "name": ["data/model.yml", "data/config.yaml", "data/notes.txt"],
                "size": [1024, 2048, 512],
            }
        )

        connector = ClickZettaConnector(**clickzetta_test_config)
        files = connector.list_volume_files("volume:user://test_volume", directory="data/")

        assert files == ["config.yaml", "model.yml"]
        assert "LIST USER VOLUME SUBDIRECTORY 'data/'" in _executed_sql(session)

        connector.close()


class TestArrowAndFrameHelpers:
    """Arrow / DataFrame / dict result helpers."""

    @patch("datus_clickzetta.connector.Session")
    def test_execute_arrow(self, mock_session_class, clickzetta_test_config):
        """execute_arrow returns an Arrow table preserving the frame's shape and column names."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})

        connector = ClickZettaConnector(**clickzetta_test_config)
        result = connector.execute_arrow("SELECT * FROM test_table")

        session.sql.assert_called_with("SELECT * FROM test_table")
        assert result.success
        assert result.row_count == 3
        assert isinstance(result.sql_return, pa.Table)
        assert result.sql_return.schema.names == ["col1", "col2"]

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_queries_arrow(self, mock_session_class, clickzetta_test_config):
        """A batch of SELECTs yields one Arrow result per query, in submission order."""
        session = _bind_session(mock_session_class)

        first = MagicMock()
        first.to_pandas.return_value = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        second = MagicMock()
        second.to_pandas.return_value = pd.DataFrame({"col3": [3, 4, 5], "col4": ["c", "d", "e"]})
        use_statement = MagicMock()
        session.sql.side_effect = [use_statement, use_statement, first, second]

        connector = ClickZettaConnector(**clickzetta_test_config)
        results = connector.execute_queries_arrow(["SELECT * FROM table1", "SELECT * FROM table2"])

        assert [result.success for result in results] == [True, True]
        assert [result.row_count for result in results] == [2, 3]
        assert [result.sql_return.schema.names for result in results] == [["col1", "col2"], ["col3", "col4"]]

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_query_to_df(self, mock_session_class, clickzetta_test_config):
        """execute_query_to_df returns the frame as-is and truncates it to max_rows when asked."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame(
            {"col1": [1, 2, 3, 4, 5], "col2": ["a", "b", "c", "d", "e"]}
        )

        connector = ClickZettaConnector(**clickzetta_test_config)

        df = connector.execute_query_to_df("SELECT * FROM test_table")
        assert list(df["col1"]) == [1, 2, 3, 4, 5]

        df_limited = connector.execute_query_to_df("SELECT * FROM test_table", max_rows=3)
        assert list(df_limited["col1"]) == [1, 2, 3]

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_query_to_dict(self, mock_session_class, clickzetta_test_config):
        """execute_query_to_dict returns one dict per row, keyed by column name."""
        session = _bind_session(mock_session_class)
        session.sql.return_value.to_pandas.return_value = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})

        connector = ClickZettaConnector(**clickzetta_test_config)
        rows = connector.execute_query_to_dict("SELECT * FROM test_table")

        assert rows == [
            {"col1": 1, "col2": "a"},
            {"col1": 2, "col2": "b"},
            {"col1": 3, "col2": "c"},
        ]

        connector.close()
