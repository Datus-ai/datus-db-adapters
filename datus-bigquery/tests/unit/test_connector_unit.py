# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from unittest.mock import MagicMock, patch

import pytest
from datus_bigquery import BigQueryConfig, BigQueryConnector


@pytest.mark.acceptance
def test_connector_initialization_with_config_object():
    """Test connector initialization with BigQueryConfig object."""
    config = BigQueryConfig(
        project="my-project",
        dataset="my_dataset",
        credentials_path="/path/to/creds.json",
        location="US",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        assert connector.config == config
        assert connector.project == "my-project"
        assert connector.credentials_path == "/path/to/creds.json"
        assert connector.location == "US"
        assert connector.catalog_name == "my-project"
        assert connector.database_name == "my_dataset"


@pytest.mark.acceptance
def test_connector_initialization_with_dict():
    """Test connector initialization with dict config."""
    config_dict = {
        "project": "my-project",
        "dataset": "analytics",
        "location": "EU",
    }

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config_dict)

        assert connector.project == "my-project"
        assert connector.catalog_name == "my-project"
        assert connector.database_name == "analytics"
        assert connector.location == "EU"


def test_connector_initialization_invalid_type():
    """Test that connector raises TypeError for invalid config type."""
    with pytest.raises(TypeError, match="config must be BigQueryConfig or dict"):
        BigQueryConnector("invalid_config")


@pytest.mark.acceptance
def test_connector_connection_string_basic():
    """Test connection string generation with project and dataset."""
    config = BigQueryConfig(
        project="my-project",
        dataset="my_dataset",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "bigquery://my-project/my_dataset" in connection_string


@pytest.mark.acceptance
def test_connector_connection_string_with_credentials():
    """Test connection string includes credentials_path."""
    config = BigQueryConfig(
        project="my-project",
        dataset="my_dataset",
        credentials_path="/path/to/creds.json",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "bigquery://my-project/my_dataset" in connection_string
        assert "credentials_path=/path/to/creds.json" in connection_string


def test_connector_connection_string_with_location():
    """Test connection string includes location."""
    config = BigQueryConfig(
        project="my-project",
        dataset="my_dataset",
        location="US",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "location=US" in connection_string


def test_connector_connection_string_with_all_params():
    """Test connection string with both credentials_path and location."""
    config = BigQueryConfig(
        project="my-project",
        dataset="my_dataset",
        credentials_path="/path/to/creds.json",
        location="EU",
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert "bigquery://my-project/my_dataset?" in connection_string
        assert "credentials_path=/path/to/creds.json" in connection_string
        assert "location=EU" in connection_string


def test_connector_connection_string_no_dataset():
    """Test connection string generation without dataset."""
    config = BigQueryConfig(
        project="my-project",
        dataset=None,
    )

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        connection_string = call_args[0][0]

        assert connection_string == "bigquery://my-project"


def test_connector_dialect_passed_to_super():
    """Test that dialect='bigquery' is passed to SQLAlchemyConnector."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__") as mock_init:
        BigQueryConnector(config)

        call_args = mock_init.call_args
        assert call_args[1]["dialect"] == "bigquery"


@pytest.mark.acceptance
def test_sys_databases():
    """Test _sys_databases returns correct system databases."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        sys_dbs = connector._sys_databases()

        assert sys_dbs == {"INFORMATION_SCHEMA", "information_schema"}
        assert isinstance(sys_dbs, set)


def test_sys_schemas():
    """Test _sys_schemas returns same as _sys_databases."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        sys_schemas = connector._sys_schemas()
        sys_dbs = connector._sys_databases()

        assert sys_schemas == sys_dbs


@pytest.mark.acceptance
def test_quote_identifier_basic():
    """Test _quote_identifier with basic identifier."""
    assert BigQueryConnector._quote_identifier("table_name") == "`table_name`"


@pytest.mark.acceptance
def test_quote_identifier_with_backticks():
    """Test _quote_identifier escapes backticks."""
    assert BigQueryConnector._quote_identifier("table`name") == "`table\\`name`"


def test_quote_identifier_empty_string():
    """Test _quote_identifier with empty string."""
    assert BigQueryConnector._quote_identifier("") == "``"


def test_quote_identifier_special_characters():
    """Test _quote_identifier with special characters."""
    assert BigQueryConnector._quote_identifier("table-name_123") == "`table-name_123`"


@pytest.mark.acceptance
def test_full_name_with_project_and_dataset():
    """Test full_name with project, dataset and table."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        full_name = connector.full_name(
            catalog_name="my-project",
            database_name="my_dataset",
            table_name="my_table",
        )

        assert full_name == "`my-project`.`my_dataset`.`my_table`"


def test_full_name_uses_defaults():
    """Test full_name uses connector defaults for project and dataset."""
    config = BigQueryConfig(project="default-project", dataset="default_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        full_name = connector.full_name(table_name="my_table")

        assert full_name == "`default-project`.`default_dataset`.`my_table`"


def test_full_name_dataset_only():
    """Test full_name with dataset and table only (no project)."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        # Override catalog_name to empty
        connector.catalog_name = ""
        full_name = connector.full_name(database_name="my_dataset", table_name="my_table")

        assert full_name == "`my_dataset`.`my_table`"


def test_full_name_table_only():
    """Test full_name with table only."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        connector.catalog_name = ""
        connector.database_name = ""
        full_name = connector.full_name(table_name="my_table")

        assert full_name == "`my_table`"


@pytest.mark.acceptance
def test_identifier_with_project_and_dataset():
    """Test identifier method with project and dataset."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        identifier = connector.identifier(
            catalog_name="my-project",
            database_name="my_dataset",
            table_name="my_table",
        )

        assert identifier == "my-project.my_dataset.my_table"


def test_identifier_uses_defaults():
    """Test identifier uses connector defaults."""
    config = BigQueryConfig(project="default-project", dataset="default_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        identifier = connector.identifier(table_name="my_table")

        assert identifier == "default-project.default_dataset.my_table"


def test_identifier_dataset_only():
    """Test identifier with dataset and table only."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        connector.catalog_name = ""
        identifier = connector.identifier(database_name="my_dataset", table_name="my_table")

        assert identifier == "my_dataset.my_table"


def test_identifier_table_only():
    """Test identifier with table only."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        connector.catalog_name = ""
        connector.database_name = ""
        identifier = connector.identifier(table_name="my_table")

        assert identifier == "my_table"


@pytest.mark.acceptance
def test_get_type():
    """Test get_type returns 'bigquery'."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        assert connector.get_type() == "bigquery"


@pytest.mark.acceptance
def test_to_dict():
    """Test to_dict method."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        result = connector.to_dict()

        assert result == {
            "db_type": "bigquery",
            "project": "my-project",
            "dataset": "my_dataset",
        }


def test_get_schemas_returns_empty():
    """Test that get_schemas returns empty list (BigQuery has no schema layer)."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        schemas = connector.get_schemas()

        assert schemas == []


@pytest.mark.acceptance
def test_do_switch_context():
    """Test do_switch_context updates project and dataset."""
    config = BigQueryConfig(project="original-project", dataset="original_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        connector.do_switch_context(catalog_name="new-project", database_name="new_dataset")

        assert connector.catalog_name == "new-project"
        assert connector.database_name == "new_dataset"


def test_do_switch_context_partial_update():
    """Test do_switch_context with partial update (only dataset)."""
    config = BigQueryConfig(project="my-project", dataset="original_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        connector.do_switch_context(database_name="new_dataset")

        assert connector.catalog_name == "my-project"
        assert connector.database_name == "new_dataset"


def test_do_switch_context_no_change():
    """Test do_switch_context with empty params preserves state."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        connector.do_switch_context()

        assert connector.catalog_name == "my-project"
        assert connector.database_name == "my_dataset"


def test_connector_stores_config():
    """Test that connector stores the config object."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        assert connector.config == config
        assert isinstance(connector.config, BigQueryConfig)


def test_connector_database_name_empty_when_none():
    """Test that database_name is empty string when config.dataset is None."""
    config = BigQueryConfig(project="my-project", dataset=None)

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        assert connector.database_name == ""


def test_sqlalchemy_schema_returns_dataset():
    """Test _sqlalchemy_schema returns dataset name."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)

        assert connector._sqlalchemy_schema() == "my_dataset"
        assert connector._sqlalchemy_schema(database_name="other") == "other"


def test_connect_uses_null_pool():
    """Test connect() creates engine with NullPool."""
    config = BigQueryConfig(project="my-project", dataset="my_dataset")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        connector.engine = None
        connector.connection_string = "bigquery://my-project/my_dataset"

        with patch("datus_bigquery.connector.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            connector.connect()

            mock_create_engine.assert_called_once()
            call_kwargs = mock_create_engine.call_args[1]
            from sqlalchemy.pool import NullPool

            assert call_kwargs["poolclass"] is NullPool
            assert connector.engine is mock_engine
            assert connector._owns_engine is True


def test_connect_skips_if_engine_exists():
    """Test connect() is a no-op when engine already exists."""
    config = BigQueryConfig(project="my-project")

    with patch("datus_sqlalchemy.SQLAlchemyConnector.__init__", return_value=None):
        connector = BigQueryConnector(config)
        mock_engine = MagicMock()
        connector.engine = mock_engine

        with patch("datus_bigquery.connector.create_engine") as mock_create_engine:
            connector.connect()

            mock_create_engine.assert_not_called()
            assert connector.engine is mock_engine
