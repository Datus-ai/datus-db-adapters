# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Apache Hive retains its own type IDs when Inceptor support is installed."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "expression,type_name,expected",
    [
        ("CAST('hive' AS VARCHAR(20))", "VARCHAR_TYPE", "hive"),
        ("CAST('fixed' AS CHAR(5))", "CHAR_TYPE", "fixed"),
        ("INTERVAL '1-2' YEAR TO MONTH", "INTERVAL_YEAR_MONTH_TYPE", "1-2"),
        ("INTERVAL '2 03:04:05.006' DAY TO SECOND", "INTERVAL_DAY_TIME_TYPE", "2 03:04:05.006000000"),
    ],
)
def test_apache_hive_type_mapping(connector, expression, type_name, expected):
    with connector._ensure_engine().connect() as connection:
        cursor = connection.connection.dbapi_connection.cursor()
        try:
            cursor.execute(f"SELECT {expression} AS value")
            assert cursor.description[0][1] == type_name
            assert cursor.fetchall() == [(expected,)]
        finally:
            cursor.close()
