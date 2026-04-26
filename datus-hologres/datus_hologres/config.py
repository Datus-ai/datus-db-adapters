# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import Field

from datus_postgresql import PostgreSQLConfig


class HologresConfig(PostgreSQLConfig):
    """Hologres-specific configuration.

    Hologres is PostgreSQL-compatible, so this model keeps the PostgreSQL
    connection shape while documenting Hologres-specific intent.
    """

    host: str = Field(default="127.0.0.1", description="Hologres instance host")
    port: int = Field(default=5432, description="Hologres PostgreSQL-compatible port")
    username: str = Field(..., description="Hologres username")
    password: str = Field(
        default="",
        description="Hologres password",
        json_schema_extra={"input_type": "password"},
    )
    database: Optional[str] = Field(default=None, description="Default Hologres database name")
    schema_name: Optional[str] = Field(default="public", alias="schema", description="Default Hologres schema name")
    sslmode: str = Field(
        default="prefer",
        description="PostgreSQL-compatible SSL mode for Hologres",
    )
    timeout_seconds: int = Field(default=30, description="Hologres connection timeout in seconds")
