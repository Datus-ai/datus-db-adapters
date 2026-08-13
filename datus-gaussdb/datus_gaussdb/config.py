# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Literal

from pydantic import Field

from datus_postgresql import PostgreSQLConfig


class GaussDBConfig(PostgreSQLConfig):
    """GaussDB-specific configuration.

    GaussDB speaks the PostgreSQL wire protocol, so all connection fields are
    inherited from PostgreSQLConfig.
    """

    driver: Literal["gaussdb", "psycopg2"] = Field(
        default="gaussdb",
        description=(
            "Client driver. 'gaussdb' (official driver, supports sha256/md5/sm3 "
            "authentication) or 'psycopg2' (escape hatch; requires md5 "
            "authentication on the server)"
        ),
    )
