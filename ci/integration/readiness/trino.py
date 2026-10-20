from datus_trino import TrinoConnector
from datus_trino.testing import config_from_env

from ._common import require_connection

config = config_from_env().model_copy(update={"timeout_seconds": 5})
require_connection("trino", TrinoConnector(config))
