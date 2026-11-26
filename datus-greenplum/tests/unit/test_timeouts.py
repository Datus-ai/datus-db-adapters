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
    from datus_greenplum import GreenplumConnector
    from datus_postgresql.testing import assert_engine_timeout_configuration

    connector = GreenplumConnector(
        {"host": "db.test", "username": "reader", "password": "test", "database": "app", **overrides}
    )
    try:
        for engine in (connector._ensure_engine(), connector._get_engine("other_database")):
            assert_engine_timeout_configuration(
                engine, connect_seconds=expected[0], read_seconds=expected[1], query_seconds=expected[2]
            )
    finally:
        connector.close()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, True),
        ({"aurora": True, "sslrootcert": "/tmp/ca.pem"}, False),
    ],
)
def test_query_timeout_override_capability_and_scope(overrides, expected):
    from unittest.mock import Mock

    from datus_greenplum import GreenplumConnector

    connector = GreenplumConnector(
        {"host": "db.test", "username": "reader", "password": "test", "database": "app", **overrides}
    )
    try:
        assert connector.supports_query_timeout_override is expected
        if expected:
            conn = Mock()
            applied = Mock()
            conn.exec_driver_sql.return_value = applied
            with connector._query_timeout_scope(conn, 6):
                pass
            conn.exec_driver_sql.assert_called_once_with("SET LOCAL statement_timeout = 6000")
            applied.close.assert_called_once_with()
    finally:
        connector.close()
