# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PostgreSQLConfig(BaseModel):
    """PostgreSQL-specific configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str = Field(default="127.0.0.1", description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port")
    username: str = Field(..., description="PostgreSQL username")
    password: str = Field(default="", description="PostgreSQL password", json_schema_extra={"input_type": "password"})
    database: Optional[str] = Field(default=None, description="Default database name")
    schema_name: Optional[str] = Field(default="public", alias="schema", description="Default schema name")
    sslmode: str = Field(
        default="prefer", description="SSL mode (disable, allow, prefer, require, verify-ca, verify-full)"
    )
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")
    enable_vector_search: bool = Field(default=True, description="Enable pgvector extension for vector similarity search")
