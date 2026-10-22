# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClickZettaConfig(BaseModel):
    """ClickZetta-specific configuration."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(..., description="ClickZetta service endpoint")
    username: str = Field(..., description="ClickZetta username")
    password: str = Field(
        ...,
        description="ClickZetta password",
        json_schema_extra={"input_type": "password"},
    )
    instance: str = Field(..., description="ClickZetta instance identifier")
    workspace: str = Field(..., description="ClickZetta workspace name")
    schema_name: str = Field(default="PUBLIC", description="Default schema name", alias="schema")
    vcluster: str = Field(default="DEFAULT_AP", description="Virtual cluster name")
    secure: Optional[bool] = Field(default=None, description="Enable secure connection")
    hints: Optional[Dict[str, Any]] = Field(default=None, description="Additional connection hints")
    extra: Optional[Dict[str, Any]] = Field(default=None, description="Extra connection parameters")
    timeout_seconds: int = Field(default=30, gt=0, description="HTTP connection/read and default SDK job timeout")
    query_timeout_seconds: int | None = Field(default=None, gt=0, description="Override SDK job execution timeout")

    @field_validator("service", "username", "password", "instance", "workspace")
    @classmethod
    def validate_non_empty_strings(cls, v):
        """Validate that required string fields are not empty."""
        if not v or not v.strip():
            raise ValueError("Required field cannot be empty")
        return v
