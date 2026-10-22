import pytest


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, (30, 30, 30)),
        ({"timeout_seconds": 7}, (7, 7, 7)),
        ({"timeout_seconds": 7, "read_timeout_seconds": 3, "query_timeout_seconds": 2}, (7, 3, 2)),
        (
            {"timeout_seconds": 7, "connect_timeout_seconds": 2, "read_timeout_seconds": 3, "query_timeout_seconds": 2},
            (2, 3, 2),
        ),
    ],
)
def test_connector_timeout_wiring_in_base_and_database_engines(overrides, expected):
    from datus_hologres import HologresConnector
    from datus_postgresql.testing import assert_engine_timeout_configuration

    connector = HologresConnector(
        {"host": "db.test", "username": "reader", "password": "test", "database": "app", **overrides}
    )
    try:
        for engine in (connector._ensure_engine(), connector._get_engine("other_database")):
            assert_engine_timeout_configuration(
                engine, connect_seconds=expected[0], read_seconds=expected[1], query_seconds=expected[2]
            )
    finally:
        connector.close()
