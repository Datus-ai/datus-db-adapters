# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import sys
from typing import Literal

from pydantic import Field

from datus_postgresql import PostgreSQLConfig


def _default_driver() -> Literal["gaussdb", "psycopg2"]:
    """Use the portable wire-compatible client when Darwin lacks GaussDB libpq."""
    return "psycopg2" if sys.platform == "darwin" else "gaussdb"


class GaussDBConfig(PostgreSQLConfig):
    """GaussDB-specific configuration.

    GaussDB speaks the PostgreSQL wire protocol, so all connection fields are
    inherited from PostgreSQLConfig.
    """

    driver: Literal["gaussdb", "psycopg2"] = Field(
        default_factory=_default_driver,
        description=(
            "Client driver. 'gaussdb' (official driver, supports sha256/md5/sm3 "
            "authentication; default outside macOS) or 'psycopg2' (macOS "
            "default and portable escape hatch; requires md5 authentication "
            "on the server)"
        ),
    )
