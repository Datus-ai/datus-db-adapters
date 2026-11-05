"""Live resolver-mode consistency across pooled Thrift sessions."""

from contextvars import Context

import pytest

pytestmark = pytest.mark.integration


def test_query_replays_the_bound_resolver_mode_on_a_different_pooled_session(connector):
    assert connector.execute_content_set("SET spark.sql.caseSensitive=false").success
    engine = connector._ensure_engine()
    first, second = engine.connect(), engine.connect()
    try:
        first.exec_driver_sql("SET spark.sql.caseSensitive=false").close()
        second.exec_driver_sql("SET spark.sql.caseSensitive=true").close()
    finally:
        first.close()
        second.close()
    assert connector.get_identifier_case_sensitive() is False
    # In true mode Orders would be an unresolved physical relation, rather than
    # the lowercase CTE. QueuePool serves the other session on the next checkout.
    result = connector.execute_query("WITH orders AS (SELECT 1 AS id) SELECT * FROM Orders", result_format="list")
    assert result.success, result.error
    assert result.sql_return == [{"id": 1}]
    assert connector.execute_content_set("SET spark.sql.caseSensitive=true").success
    assert connector.get_identifier_case_sensitive() is True
    assert Context().run(connector.get_identifier_case_sensitive) is connector.get_default_identifier_case_sensitive()
    assert connector.get_identifier_case_sensitive() is True


def test_finishing_a_stream_does_not_undo_a_later_resolver_mode_change(connector):
    assert connector.execute_content_set("SET spark.sql.caseSensitive=false").success
    iterator = connector.execute_csv_iterator("SELECT 1 AS id", with_header=False)
    try:
        assert next(iterator) == (1,)
        assert connector.execute_content_set("SET spark.sql.caseSensitive=true").success
        assert list(iterator) == []
        assert connector.get_identifier_case_sensitive() is True
    finally:
        iterator.close()
