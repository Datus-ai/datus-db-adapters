# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TiDBConfig(BaseModel):
    """TiDB-specific configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="TiDB server host")
    port: int = Field(default=4000, description="TiDB server port")
    username: str = Field(..., description="TiDB username")
    password: str = Field(
        default="",
        description="TiDB password",
        json_schema_extra={"input_type": "password"},
    )
    database: Optional[str] = Field(default=None, description="Default database name")
    charset: str = Field(default="utf8mb4", description="Character set to use")
    autocommit: bool = Field(default=True, description="Enable autocommit mode")
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")
