# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import sys
from typing import Literal, Optional

from pydantic import Field

from datus_postgresql import PostgreSQLConfig


def _default_driver() -> Literal["gaussdb", "pg8000"]:
    """The official driver binds the GaussDB libpq, which has no Darwin build.

    macOS therefore defaults to the pure-Python ``pg8000`` path, which speaks
    the GaussDB SHA256 handshake natively and works against a stock server.
    """
    return "pg8000" if sys.platform == "darwin" else "gaussdb"


class GaussDBConfig(PostgreSQLConfig):
    """GaussDB-specific configuration.

    GaussDB speaks the PostgreSQL wire protocol, so all connection fields are
    inherited from PostgreSQLConfig.
    """

    driver: Literal["gaussdb", "pg8000", "psycopg2"] = Field(
        default_factory=_default_driver,
        description=(
            "Client driver. 'gaussdb' (official driver; sha256/md5/sm3 "
            "authentication; default outside macOS), 'pg8000' (pure Python; "
            "sha256/md5 authentication on every platform; macOS default) or "
            "'psycopg2' (escape hatch; requires md5 authentication on the "
            "server)"
        ),
    )
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = Field(
        default="prefer",
        description=(
            "TLS mode. 'verify-ca' is recommended for production. pg8000 requires "
            "sslrootcert; the libpq drivers may use their standard certificate locations"
        ),
    )
    sslrootcert: Optional[str] = Field(
        default=None,
        description=(
            "CA certificate used to verify the server for sslmode "
            "verify-ca/verify-full — either a path or the PEM content itself, so a "
            "caller with no filesystem on the server can supply an uploaded "
            "certificate. Forwarded to every supported driver; the libpq-based "
            "drivers can also use their standard certificate locations"
        ),
    )
