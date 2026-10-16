import pytest

from datus_hana import HanaConnector
from datus_hana.dialect_operations import HanaDialectOperations


def test_migration_explicitly_unsupported():
    connector = HanaConnector.__new__(HanaConnector)
    assert connector.describe_migration_capabilities() == {
        "supported": False,
        "dialect_family": "hana",
        "reason": "Lossless migration DDL and arbitrary HANA SQL analysis are not implemented",
    }
    assert connector.map_source_type("postgres", "integer") is None
    assert connector.suggest_table_layout([]) == {}
    assert connector.validate_ddl("CREATE TABLE t (x INT)") == []


def test_fixed_sql_operations():
    ops = HanaDialectOperations()
    assert ops.render_limit("SELECT * FROM t", 3) == "SELECT * FROM t LIMIT 3"
    assert ops.render_count("SELECT * FROM t", "q") == 'SELECT COUNT(*) AS __datus_count FROM (SELECT * FROM t) AS "q"'
    assert ops.quote_identifier('Mixed"Case') == '"Mixed""Case"'
    with pytest.raises(ValueError):
        ops.render_limit("SELECT 1", -1)
    with pytest.raises(NotImplementedError):
        ops.write_dataframe(None, "t", None, 1)
    with pytest.raises(NotImplementedError):
        ops.infer_transfer_type(None)
