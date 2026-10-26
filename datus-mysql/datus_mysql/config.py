# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from datus_sqlalchemy.auth import DatabaseAuthConfig


class MySQLTimeoutConfig(BaseModel):
    """Timeout policy shared by adapters using the MySQL wire protocol."""

    timeout_seconds: int = Field(
        default=30, ge=1, description="Pool checkout timeout and default DBAPI connect/read/write timeout in seconds"
    )
    connect_timeout_seconds: Optional[int] = Field(default=None, ge=1, description="Override the DBAPI connect timeout")
    read_timeout_seconds: Optional[int] = Field(default=None, ge=1, description="Override each socket read timeout")
    write_timeout_seconds: Optional[int] = Field(default=None, ge=1, description="Override each socket write timeout")
    query_timeout_seconds: Optional[int] = Field(
        default=None, ge=1, description="Optional server-side SELECT deadline; None preserves the server setting"
    )


class MySQLConfig(DatabaseAuthConfig, MySQLTimeoutConfig):
    """MySQL-specific configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="MySQL server host")
    port: int = Field(default=3306, description="MySQL server port")
    username: str = Field(..., description="MySQL username")
    password: str = Field(
        default="",
        repr=False,
        description="MySQL password",
        json_schema_extra={"input_type": "password"},
    )
    database: Optional[str] = Field(default=None, description="Default database name")
    charset: str = Field(default="utf8mb4", description="Character set to use")
    autocommit: bool = Field(default=True, description="Enable autocommit mode")
    ssl_ca: Optional[str] = None
    ssl_cert: Optional[str] = None
    ssl_key: Optional[str] = Field(default=None, repr=False)
    ssl_verify_cert: Optional[bool] = None
    ssl_verify_identity: Optional[bool] = None

    @model_validator(mode="after")
    def validate_tls(self):
        if bool(self.ssl_cert) != bool(self.ssl_key):
            raise ValueError("ssl_cert and ssl_key must be supplied together")
        if self.aurora or self.auth_mode == "iam":
            if not self.ssl_ca or self.ssl_verify_cert is False or self.ssl_verify_identity is False:
                raise ValueError("Aurora/IAM requires ssl_ca and certificate/hostname verification")
            self.ssl_verify_cert = True
            self.ssl_verify_identity = True
        if (self.ssl_verify_cert or self.ssl_verify_identity) and not self.ssl_ca:
            raise ValueError("Certificate verification requires ssl_ca")
        return self
