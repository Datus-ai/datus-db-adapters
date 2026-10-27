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


_SCALARS = [
    ("TO_DECIMAL", "score"),
    ("TO_BIGINT", "score"),
    ("WEEKDAY", "happened_at"),
    ("ADD_YEARS", "happened_at, 1"),
    ("HOUR", "happened_at"),
    ("MINUTE", "happened_at"),
]


@pytest.mark.parametrize("name,arguments", _SCALARS)
def test_dosi_native_scalars_preserve_physical_tables(name, arguments):
    result = analyze_hana_sql(f'SELECT {name.lower()}({arguments}) FROM "Sales"."Events"')
    assert result.require_complete().sql_type == SQLType.SELECT
    assert result.tables == ('"Sales"."Events"',)


@pytest.mark.parametrize("name,arguments", _SCALARS)
@pytest.mark.parametrize("form", ["qualified", "quoted", "table", "unknown_argument", "sequence_argument"])
def test_verified_scalar_names_do_not_hide_unverified_object_access(name, arguments, form):
    call = f"{name}({arguments})"
    if form == "qualified":
        sql = f"SELECT s.{call} FROM events"
    elif form == "quoted":
        sql = f'SELECT "{name}"({arguments}) FROM events'
    elif form == "table":
        sql = f"SELECT * FROM {call}"
    else:
        replacement = "private_function()" if form == "unknown_argument" else "app_seq.NEXTVAL"
        arguments = arguments.replace("score", replacement).replace("happened_at", replacement)
        sql = f"SELECT {name}({arguments}) FROM events"
    result = analyze_hana_sql(sql)
    assert result.status in {"error", "unsupported"}, result
    with pytest.raises(ValueError, match="SQL analysis"):
        result.require_complete()


def test_scalar_subquery_keeps_its_physical_table_visible():
    result = analyze_hana_sql("SELECT TO_DECIMAL((SELECT MAX(score) FROM private.events)) FROM DUMMY")
    assert result.require_complete().tables == ('"PRIVATE"."EVENTS"', '"DUMMY"')


def test_nested_aggregate_conversion_and_calendar_calls_keep_the_source_table():
    sql = """WITH x AS (
        SELECT AVG(TO_DECIMAL(score)) AS score, ADD_YEARS(happened_at, 1) AS next_year
        FROM events GROUP BY happened_at
    ) SELECT TO_BIGINT(score), WEEKDAY(next_year), HOUR(next_year), MINUTE(next_year) FROM x"""
    assert analyze_hana_sql(sql).require_complete().tables == ('"EVENTS"',)


@pytest.mark.parametrize("name", ["SUM", "COUNT", "ABS", "CURRENT_DATE", "CURRENT_TIMESTAMP"])
def test_quoted_builtin_names_remain_unverified_user_calls(name):
    assert analyze_hana_sql(f'SELECT "{name}"(score) FROM events').status == "unsupported"


def test_quoted_cte_columns_and_aliases_are_not_function_calls():
    sql = '''WITH "x"("score") AS (SELECT score FROM "Sales"."Events")
        SELECT AVG(TO_DECIMAL("score")) AS "mean" FROM "x"'''
    assert analyze_hana_sql(sql).require_complete().tables == ('"Sales"."Events"',)


def test_quoted_no_parentheses_function_name_is_a_column():
    assert analyze_hana_sql('SELECT "CURRENT_DATE" FROM events').require_complete().tables == ('"EVENTS"',)


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
