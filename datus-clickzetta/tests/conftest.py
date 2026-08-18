# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Shared test configuration and fixtures for ClickZetta adapter tests."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(scope="session")
def mock_datus_modules():
    """Mock datus modules for the entire test session."""
    # Store original modules
    original_modules = {}
    datus_modules = [key for key in sys.modules if key.startswith("datus")]
    for module in datus_modules:
        original_modules[module] = sys.modules.get(module)

    # Create mock modules
    mock_datus = MagicMock()

    # Mock schemas.base
    mock_schemas_base = MagicMock()
    mock_schemas_base.TABLE_TYPE = "table"

    # Mock node_models
    mock_node_models = MagicMock()

    class MockExecuteSQLResult:
        def __init__(
            self,
            success=True,
            error="",
            sql_query="",
            sql_return="",
            row_count=0,
            result_format="csv",
            data=None,
            error_message=None,
        ):
            self.success = success
            self.error = error
            self.sql_query = sql_query
            self.sql_return = sql_return
            self.row_count = row_count
            self.result_format = result_format
            self.data = data
            self.error_message = error_message

    mock_node_models.ExecuteSQLResult = MockExecuteSQLResult

    # Mock db_tools.base
    class MockBaseSqlConnector:
        def __init__(self, db_type):
            self.db_type = db_type
            self.connection = None

        def close(self):
            pass

        @property
        def dialect(self):
            return "clickzetta"

    mock_base = MagicMock()
    mock_base.BaseSqlConnector = MockBaseSqlConnector

    # Mock constants
    class MockDBType:
        CLICKZETTA = "clickzetta"
        value = "clickzetta"

    class MockSQLType:
        SELECT = "SELECT"
        INSERT = "INSERT"
        UPDATE = "UPDATE"
        DELETE = "DELETE"
        DDL = "DDL"

    mock_constants = MagicMock()
    mock_constants.DBType = MockDBType
    mock_constants.SQLType = MockSQLType

    # Mock exceptions
    class MockDatusException(Exception):
        def __init__(self, code=None, message="", message_args=None, error_code=None):
            self.code = error_code or code  # Support both parameter names
            self.error_code = error_code or code
            self.message = message
            self.message_args = message_args or {}
            super().__init__(str(message))

    class MockErrorCode:
        COMMON_MISSING_DEPENDENCY = "COMMON_MISSING_DEPENDENCY"
        COMMON_CONFIG_ERROR = "COMMON_CONFIG_ERROR"
        DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
        DB_EXECUTION_ERROR = "DB_EXECUTION_ERROR"
        SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"

    mock_exceptions = MagicMock()
    mock_exceptions.DatusException = MockDatusException
    mock_exceptions.ErrorCode = MockErrorCode

    # Mock loggings
    mock_logger = MagicMock()
    mock_loggings = MagicMock()
    mock_loggings.get_logger = MagicMock(return_value=mock_logger)

    # Mock sql_utils
    mock_sql_utils = MagicMock()
    mock_sql_utils.metadata_identifier = MagicMock(return_value="workspace.schema.table")
    mock_sql_utils.parse_context_switch = MagicMock(return_value={})
    mock_sql_utils.parse_sql_type = MagicMock(return_value="SELECT")

    # Set up all mock modules
    sys.modules["datus"] = mock_datus
    sys.modules["datus.schemas"] = MagicMock()
    sys.modules["datus.schemas.base"] = mock_schemas_base
    sys.modules["datus.schemas.node_models"] = mock_node_models
    sys.modules["datus.tools"] = MagicMock()
    sys.modules["datus.tools.db_tools"] = MagicMock()
    sys.modules["datus.tools.db_tools.base"] = mock_base
    sys.modules["datus.utils"] = MagicMock()
    sys.modules["datus.utils.constants"] = mock_constants
    sys.modules["datus.utils.exceptions"] = mock_exceptions
    sys.modules["datus.utils.loggings"] = mock_loggings
    sys.modules["datus.utils.sql_utils"] = mock_sql_utils

    yield

    # Restore original modules
    for module, original in original_modules.items():
        if original is not None:
            sys.modules[module] = original
        else:
            sys.modules.pop(module, None)


@pytest.fixture
def clickzetta_test_config():
    """Provide standard test configuration for ClickZetta connector."""
    return {
        "service": os.getenv("CLICKZETTA_SERVICE", "test-service.clickzetta.com"),
        "username": os.getenv("CLICKZETTA_USERNAME", "testuser"),
        "password": os.getenv("CLICKZETTA_PASSWORD", "testpass"),
        "instance": os.getenv("CLICKZETTA_INSTANCE", "test_instance"),
        "workspace": os.getenv("CLICKZETTA_WORKSPACE", "test_workspace"),
        "schema": os.getenv("CLICKZETTA_SCHEMA", "PUBLIC"),
        "vcluster": os.getenv("CLICKZETTA_VCLUSTER", "DEFAULT_AP"),
    }


@pytest.fixture
def mock_clickzetta_session():
    """Provide a mock ClickZetta session for testing."""
    session_mock = MagicMock()

    # Mock common session methods
    session_mock.sql.return_value.to_pandas.return_value = MagicMock()
    session_mock.close.return_value = None

    # Mock file operations
    session_mock.file.get.return_value = None

    return session_mock


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Mark test as a unit test")
    config.addinivalue_line("markers", "integration: Mark test as an integration test")
    config.addinivalue_line(
        "markers",
        "requires_clickzetta: Mark test as requiring actual ClickZetta credentials",
    )
    config.addinivalue_line("markers", "slow: Mark test as slow running")


def pytest_collection_modifyitems(config, items):
    """Automatically mark tests based on their location and names."""
    tests_root = Path(__file__).resolve().parent
    for item in items:
        try:
            location_parts = Path(str(item.fspath)).resolve().relative_to(tests_root).parts
        except ValueError:
            location_parts = ()

        # Mark tests in unit/ directory as unit tests
        if "unit" in location_parts:
            item.add_marker(pytest.mark.unit)

        # Mark tests in integration/ directory as integration tests
        if "integration" in location_parts:
            item.add_marker(pytest.mark.integration)

        # Mark tests that require actual ClickZetta connection
        if "requires_connection" in item.name or "live" in item.name:
            item.add_marker(pytest.mark.requires_clickzetta)

        # Mark slow tests
        if "slow" in item.name or "full_" in item.name:
            item.add_marker(pytest.mark.slow)
