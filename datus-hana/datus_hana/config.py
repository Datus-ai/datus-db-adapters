# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from pydantic import ConfigDict, Field, model_validator

from datus_db_core import ConnectionConfig


class HanaConfig(ConnectionConfig):
    """Tenant is a connection target; SQL objects use schema.table only."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str | None = None
    port: int = Field(default=39041, ge=1, le=65535)
    username: str | None = None
    password: str = Field(default="", repr=False, json_schema_extra={"input_type": "password"})
    userkey: str | None = Field(default=None, repr=False)
    database: str | None = Field(default=None, description="Tenant database; fixed for this connector")
    schema_name: str | None = Field(default=None, alias="schema")
    encrypt: bool = True
    ssl_validate_certificate: bool = True
    ssl_trust_store: str | None = None
    ssl_host_name_in_certificate: str | None = None
    communication_timeout_seconds: int = Field(default=60, gt=0)
    timeout_seconds: int = Field(default=30, gt=0)
    max_result_rows: int = Field(default=10000, gt=0)
    max_result_bytes: int = Field(default=16 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_auth(self):
        if self.userkey:
            if self.username or self.password:
                raise ValueError("userkey and username/password are mutually exclusive")
        elif not self.host or not self.username or not self.password:
            raise ValueError("host, username and password are required without userkey")
        return self
