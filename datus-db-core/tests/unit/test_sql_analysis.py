import pytest

from datus_db_core.constants import SQLType
from datus_db_core.registry import connector_registry
from datus_db_core.sql_analysis import SQLAnalysis
from datus_db_core.sql_utils import parse_sql_type


def test_complete_no_table_result_and_unsupported_are_distinct():
    result = SQLAnalysis("complete", SQLType.SELECT)
    assert result.require_complete().tables == ()
    with pytest.raises(ValueError, match="unverified syntax"):
        SQLAnalysis("unsupported", diagnostic="unverified syntax").require_complete()


def test_adapter_classifier_does_not_fall_back_to_keywords(monkeypatch):
    def analyze(sql):
        return SQLAnalysis("complete", SQLType.SELECT) if sql == "SELECT 1" else SQLAnalysis("unsupported")

    monkeypatch.setattr(
        connector_registry, "get_sql_analyzer", lambda dialect: analyze if dialect == "native-test" else None
    )
    assert parse_sql_type("SELECT 1", "native-test") == SQLType.SELECT
    assert parse_sql_type("SELECT unsupported syntax", "native-test") == SQLType.UNKNOWN
    assert parse_sql_type("SELECT 1; DELETE FROM t", "native-test") == SQLType.UNKNOWN
    assert parse_sql_type("INSERT INTO t VALUES (1)", "sqlite") == SQLType.INSERT
