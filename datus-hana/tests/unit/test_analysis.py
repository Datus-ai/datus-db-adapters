import pytest

from datus_db_core.constants import SQLType
from datus_hana.analysis import analyze_hana_sql


@pytest.mark.parametrize(
    "sql,tables",
    [
        ("SELECT 1", ()),
        ("SELECT * FROM sales.orders LIMIT 2", ('"SALES"."ORDERS"',)),
        (
            'SELECT a."id", COUNT(*) FROM "Sales"."Orders" a JOIN items b ON a."id" = b.id GROUP BY a."id"',
            ('"Sales"."Orders"', '"ITEMS"'),
        ),
        ("WITH x AS (SELECT * FROM s.t) SELECT ROW_NUMBER() OVER (ORDER BY a) FROM x", ('"S"."T"',)),
        ("WITH x AS (SELECT * FROM a) SELECT * FROM x UNION ALL SELECT * FROM b", ('"A"', '"B"')),
        ("WITH x AS (SELECT 1) SELECT * FROM s.x", ('"S"."X"',)),
        ('SELECT * FROM "S"."x.y"', ('"S"."x.y"',)),
        ("SELECT 'app_seq.NEXTVAL' FROM DUMMY", ('"DUMMY"',)),
        ("SELECT TO_VARCHAR(order_date, 'YYYY-MM') FROM sales.orders", ('"SALES"."ORDERS"',)),
        ("SELECT add_days(order_date, -1) FROM sales.orders", ('"SALES"."ORDERS"',)),
        ("SELECT DAYS_BETWEEN(started_at, ended_at) FROM events", ('"EVENTS"',)),
        ("SELECT SECONDS_BETWEEN(started_at, ended_at) FROM events", ('"EVENTS"',)),
        ("WITH x AS (SELECT ADD_DAYS(d, 1) AS d FROM t) SELECT TO_VARCHAR(d) FROM x", ('"T"',)),
    ],
)
def test_common_queries(sql, tables):
    result = analyze_hana_sql(sql)
    assert result.status == "complete", result
    assert result.sql_type == SQLType.SELECT
    assert result.tables == tables


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT TOP 5 * FROM t",
        "SELECT * FROM t; DELETE FROM t",
        "",
        "SELECT * FROM",
        "SELECT * FROM f(1)",
        "SELECT * FROM a.b.c",
        "SELECT * INTO new_table FROM t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT private_function() FROM DUMMY",
        "SELECT s.TO_VARCHAR(d) FROM t",
        'SELECT "TO_VARCHAR"(d) FROM t',
        'SELECT "to_varchar"(d) FROM t',
        "SELECT * FROM ADD_DAYS(d, 1)",
        "SELECT TO_VARCHAR(private_function()) FROM DUMMY",
        "SELECT ADD_DAYS(app_seq.NEXTVAL, 1) FROM DUMMY",
        "SELECT app_seq.NEXTVAL FROM DUMMY",
        "SELECT s.app_seq.currval FROM DUMMY",
        'SELECT "S"."app_seq".NEXTVAL FROM DUMMY',
        'SELECT "app_seq"."NEXTVAL" FROM DUMMY',
        "SELECT 1 + app_seq.NEXTVAL FROM DUMMY",
        "DO BEGIN SELECT * FROM t; END",
        'SELECT * FROM "_SYS_BIC"."pkg/view"(PLACEHOLDER."$$P$$" => \'x\')',
        "WITH changed AS (DELETE FROM t RETURNING *) SELECT * FROM changed",
    ],
)
def test_unverified_sql_never_becomes_complete_empty_tables(sql):
    result = analyze_hana_sql(sql)
    assert result.status in {"error", "unsupported"}
    assert result.diagnostic
    with pytest.raises(ValueError, match="SQL analysis"):
        result.require_complete()
