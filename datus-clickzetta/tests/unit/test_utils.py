# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Unit tests for utility functions in ClickZetta connector."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.mark.usefixtures("mock_datus_modules")
class TestUtilityFunctions:
    """Test suite for standalone utility functions."""

    def test_safe_escape(self):
        """Test SQL string escaping function."""
        from datus_clickzetta.connector import _safe_escape

        # Test normal string
        assert _safe_escape("test") == "test"

        # Test string with single quotes
        assert _safe_escape("test'quote") == "test''quote"

        # Test string with multiple quotes
        assert _safe_escape("test'multiple'quotes'here") == "test''multiple''quotes''here"

        # Test None input
        assert _safe_escape(None) == ""

        # Test empty string
        assert _safe_escape("") == ""

    def test_safe_escape_identifier(self):
        """Test SQL identifier escaping function."""
        from datus_clickzetta.connector import _safe_escape_identifier

        # Test normal identifier
        assert _safe_escape_identifier("test") == "test"

        # Test identifier with backticks
        assert _safe_escape_identifier("test`backtick") == "test``backtick"

        # Test identifier with multiple backticks
        assert _safe_escape_identifier("test`multi`backticks") == "test``multi``backticks"

        # Test None input
        assert _safe_escape_identifier(None) == ""

        # Test empty string
        assert _safe_escape_identifier("") == ""

    def test_extract_row_count(self):
        """Test row count extraction from DataFrame."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Test with 'rows' column
        df = pd.DataFrame({"rows": [5], "other": [10]})
        count = ClickZettaConnector._extract_row_count(df)
        assert count == 5

        # Test with 'row_count' column
        df = pd.DataFrame({"row_count": [3], "other": [10]})
        count = ClickZettaConnector._extract_row_count(df)
        assert count == 3

        # Test with 'rows_affected' column
        df = pd.DataFrame({"rows_affected": [7], "other": [10]})
        count = ClickZettaConnector._extract_row_count(df)
        assert count == 7

        # Test with empty DataFrame
        df = pd.DataFrame()
        count = ClickZettaConnector._extract_row_count(df)
        assert count == 0

        # Test with no count columns (fallback to len)
        df = pd.DataFrame({"col1": [1, 2, 3], "col2": [4, 5, 6]})
        count = ClickZettaConnector._extract_row_count(df)
        assert count == 3

        # Test with None input
        count = ClickZettaConnector._extract_row_count(None)
        assert count == 0

    def test_sql_execution_error_logs_original_exception(self, caplog):
        from datus_clickzetta.connector import ClickZettaConnector
        from datus_db_core import DatusDbException

        connector = object.__new__(ClickZettaConnector)
        error = RuntimeError("query failed")

        try:
            raise error
        except RuntimeError as exc:
            with pytest.raises(DatusDbException):
                connector._wrap_exception(exc, "SELECT bad")

        assert "ClickZetta SQL execution failed; sql_preview='SELECT bad'; sql_chars=10" in caplog.text
        assert "query failed" in caplog.text
        assert caplog.records[-1].exc_info[2] is error.__traceback__

    def test_normalize_volume_uri(self):
        """Test volume URI normalization."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Test volume: format
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://my_volume", "path/file.yaml")
        assert uri == "volume:user://my_volume/path/file.yaml"

        # Test @ format (stage)
        uri = ClickZettaConnector._normalize_volume_uri("@my_stage", "path/file.yaml")
        assert uri == "@my_stage/path/file.yaml"

        # Test with empty relative path
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://my_volume", "")
        assert uri == "volume:user://my_volume"

        # Test with slash trimming
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://my_volume/", "/path/file.yaml")
        assert uri == "volume:user://my_volume/path/file.yaml"

        # Test error cases
        with pytest.raises(ValueError, match="Volume name must not be empty"):
            ClickZettaConnector._normalize_volume_uri("", "file.yaml")

        with pytest.raises(ValueError, match="Unsupported volume/stage format"):
            ClickZettaConnector._normalize_volume_uri("invalid_format", "file.yaml")

    @patch("datus_clickzetta.connector.Session")
    def test_ddl_definition_building(self, mock_session_class):
        """Test DDL definition building functionality."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.builder.configs.return_value.create.return_value = mock_session

        # Create connector instance
        connector = ClickZettaConnector(
            service="service",
            username="user",
            password="pass",
            instance="instance",
            workspace="workspace",
        )

        # Test simple table definition
        columns = [
            {"column_name": "id", "data_type": "INT", "comment": "Primary key"},
            {"column_name": "name", "data_type": "VARCHAR(50)"},
        ]

        definition = connector._build_definition(
            workspace="workspace",
            schema_name="schema",
            table_name="test_table",
            columns=columns,
            table_comment="Test table",
        )

        # Verify DDL components
        assert "CREATE TABLE" in definition
        assert "`workspace`.`schema`.`test_table`" in definition
        assert "`id` INT COMMENT 'Primary key'" in definition
        assert "`name` VARCHAR(50)" in definition
        assert "COMMENT = 'Test table'" in definition

        # Test definition without comments
        columns_no_comment = [
            {"column_name": "id", "data_type": "INT"},
            {"column_name": "name", "data_type": "VARCHAR(50)"},
        ]

        definition = connector._build_definition(
            workspace="workspace",
            schema_name="schema",
            table_name="simple_table",
            columns=columns_no_comment,
        )

        assert "CREATE TABLE" in definition
        assert "`workspace`.`schema`.`simple_table`" in definition
        assert "`id` INT" in definition
        assert "COMMENT 'Primary key'" not in definition

        # Test view definition
        definition = connector._build_definition(
            workspace="workspace",
            schema_name="schema",
            table_name="test_view",
            columns=columns,
            table_type="view",
        )

        assert "CREATE VIEW" in definition

        connector.close()


class TestVolumeOperations:
    """Test suite for volume-related operations."""

    def test_volume_uri_edge_cases(self):
        """Test edge cases for volume URI handling."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Test with trailing slashes
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://test/", "file.txt")
        assert uri == "volume:user://test/file.txt"

        # Test with leading slashes in path
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://test", "/file.txt")
        assert uri == "volume:user://test/file.txt"

        # Test stage with trailing slash
        uri = ClickZettaConnector._normalize_volume_uri("@stage/", "file.txt")
        assert uri == "@stage/file.txt"

        # Test nested paths
        uri = ClickZettaConnector._normalize_volume_uri("volume:user://vol", "dir1/dir2/file.txt")
        assert uri == "volume:user://vol/dir1/dir2/file.txt"


class TestNewMethods:
    """Test suite for new methods added for Snowflake compatibility."""

    @patch("datus_clickzetta.connector.Session")
    def test_execute_arrow_empty_result(self, mock_session_class):
        """Test execute_arrow with empty result."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.builder.configs.return_value.create.return_value = mock_session

        # Mock empty DataFrame
        empty_df = pd.DataFrame()
        mock_session.sql.return_value.to_pandas.return_value = empty_df

        connector = ClickZettaConnector(
            service="service",
            username="user",
            password="pass",
            instance="instance",
            workspace="workspace",
        )

        result = connector.execute_arrow("SELECT * FROM empty_table")

        assert result.success
        assert result.row_count == 0
        import pyarrow as pa

        assert isinstance(result.sql_return, pa.Table)
        assert result.sql_return.num_rows == 0

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_query_to_df_max_rows(self, mock_session_class):
        """Test execute_query_to_df with max_rows parameter."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.builder.configs.return_value.create.return_value = mock_session

        # Mock large DataFrame
        large_df = pd.DataFrame({"id": range(100), "value": [f"value_{i}" for i in range(100)]})
        mock_session.sql.return_value.to_pandas.return_value = large_df

        connector = ClickZettaConnector(
            service="service",
            username="user",
            password="pass",
            instance="instance",
            workspace="workspace",
        )

        # Test with max_rows limitation
        df = connector.execute_query_to_df("SELECT * FROM large_table", max_rows=10)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10
        assert list(df["id"]) == list(range(10))

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_query_to_dict_empty(self, mock_session_class):
        """Test execute_query_to_dict with empty result."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.builder.configs.return_value.create.return_value = mock_session

        # Mock empty DataFrame
        empty_df = pd.DataFrame()
        mock_session.sql.return_value.to_pandas.return_value = empty_df

        connector = ClickZettaConnector(
            service="service",
            username="user",
            password="pass",
            instance="instance",
            workspace="workspace",
        )

        result = connector.execute_query_to_dict("SELECT * FROM empty_table")

        assert isinstance(result, list)
        assert len(result) == 0

        connector.close()

    @patch("datus_clickzetta.connector.Session")
    def test_execute_arrow_data_conversion(self, mock_session_class):
        """Test execute_arrow data conversion to Arrow format."""
        from datus_clickzetta.connector import ClickZettaConnector

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.builder.configs.return_value.create.return_value = mock_session

        # Mock query result
        mock_df = pd.DataFrame(
            {
                "int_col": [1, 2, 3],
                "str_col": ["a", "b", "c"],
                "float_col": [1.1, 2.2, 3.3],
            }
        )
        mock_session.sql.return_value.to_pandas.return_value = mock_df

        connector = ClickZettaConnector(
            service="service",
            username="user",
            password="pass",
            instance="instance",
            workspace="workspace",
        )

        result = connector.execute_arrow("SELECT * FROM test_table")

        assert result.success
        assert result.row_count == 3

        # Verify data is an Arrow table with correct structure
        import pyarrow as pa

        assert isinstance(result.sql_return, pa.Table)
        assert result.sql_return.num_rows == 3
        assert result.sql_return.num_columns == 3

        # Verify column names are preserved
        expected_columns = ["int_col", "str_col", "float_col"]
        assert result.sql_return.schema.names == expected_columns

        connector.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
