# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SparkConfig(BaseModel):
    """Spark SQL (via HiveServer2/Thrift) specific configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="Spark Thrift Server host")
    port: int = Field(default=10000, description="Spark Thrift Server port")
    username: str = Field(..., description="Spark username")
    password: str = Field(
        default="",
        description="Spark password",
        json_schema_extra={"input_type": "password"},
    )
    database: Optional[str] = Field(default=None, description="Default database name")
    auth_mechanism: Literal["NONE", "NOSASL", "LDAP", "KERBEROS", "CUSTOM"] = Field(
        default="NONE", description="Authentication mechanism (NONE, NOSASL, LDAP, KERBEROS, CUSTOM)"
    )
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")
