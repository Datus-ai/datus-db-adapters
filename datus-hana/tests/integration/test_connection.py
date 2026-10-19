import pytest

from datus_hana import HanaConnector

pytestmark = pytest.mark.integration


def test_config_dict_and_context_manager(config):
    with HanaConnector(config.model_dump()) as connector:
        assert connector.test_connection()
        assert connector.get_current_context() == {
            "catalog_name": "",
            "database_name": "",
            "schema_name": config.schema_name,
        }


def test_select_uses_exact_quoted_alias_case(connector):
    """HANA folds unquoted aliases to uppercase but preserves quoted aliases."""
    result = connector.execute_query('SELECT 1 AS "Mixed Alias", 2 AS plain_alias FROM DUMMY', "list")
    assert result.success, result.error
    assert result.sql_return == [{"Mixed Alias": 1, "PLAIN_ALIAS": 2}]
