import pytest

from datus_sqlalchemy.testing import assert_stalled_response_timeout
from datus_tidb import TiDBConnector

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation", ["query", "metadata", "test_connection"])
def test_real_server_silent_response_is_bounded(config, operation, connector):
    assert_stalled_response_timeout(
        config,
        TiDBConnector,
        operation,
    )
