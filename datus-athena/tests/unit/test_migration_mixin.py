from datus_athena import AthenaConnector


def test_migration_is_explicitly_unsupported():
    connector = AthenaConnector.__new__(AthenaConnector)
    caps = connector.describe_migration_capabilities()
    assert caps["supported"] is False
    assert caps["dialect_family"] == "athena"
    assert connector.suggest_table_layout([]) == {}
    assert connector.validate_ddl("CREATE TABLE example (id int)") == []
    assert connector.map_source_type("mysql", "INT") is None
