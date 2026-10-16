from datus_hana import HanaConnector
from datus_hana.testing import config_from_env

from ._common import require_connection

require_connection("hana", HanaConnector(config_from_env()))
