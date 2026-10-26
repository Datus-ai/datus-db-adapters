# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import ConfigDict, Field, model_validator

from datus_sqlalchemy.auth import DatabaseAuthConfig


class PostgreSQLConfig(DatabaseAuthConfig):
    """PostgreSQL-specific configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str = Field(default="127.0.0.1", description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port")
    username: str = Field(..., description="PostgreSQL username")
    password: str = Field(
        default="",
        repr=False,
        description="PostgreSQL password",
        json_schema_extra={"input_type": "password"},
    )
    database: Optional[str] = Field(default=None, description="Default database name")
    schema_name: Optional[str] = Field(default="public", alias="schema", description="Default schema name")
    sslmode: str = Field(
        default="prefer",
        description="SSL mode (disable, allow, prefer, require, verify-ca, verify-full)",
    )
    timeout_seconds: int = Field(
        default=30, gt=0, description="Pool, socket-operation and default query deadline in seconds"
    )
    read_timeout_seconds: Optional[int] = Field(
        default=None, gt=0, description="Override the connection checkout deadline"
    )
    query_timeout_seconds: Optional[int] = Field(
        default=None, gt=0, description="Override the server statement timeout"
    )
    sslrootcert: Optional[str] = None
    sslcert: Optional[str] = None
    sslkey: Optional[str] = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_tls(self):
        if bool(self.sslcert) != bool(self.sslkey):
            raise ValueError("sslcert and sslkey must be supplied together")
        if self.aurora or self.auth_mode == "iam":
            if not self.sslrootcert:
                raise ValueError("Aurora/IAM requires sslrootcert")
            if "sslmode" in self.model_fields_set and self.sslmode != "verify-full":
                raise ValueError("Aurora/IAM requires sslmode=verify-full")
            self.sslmode = "verify-full"
        return self
