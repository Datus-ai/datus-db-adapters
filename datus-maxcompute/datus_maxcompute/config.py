# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator


class MaxComputeConfig(BaseModel):
    """Connection and execution settings for Alibaba Cloud MaxCompute."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project: str = Field(
        ...,
        validation_alias=AliasChoices("project", "database"),
        description="MaxCompute project name",
    )
    endpoint: str = Field(..., description="MaxCompute service endpoint")
    access_key_id: SecretStr = Field(
        ...,
        description="Alibaba Cloud AccessKey ID",
        json_schema_extra={"input_type": "password"},
    )
    access_key_secret: SecretStr = Field(
        ...,
        description="Alibaba Cloud AccessKey secret",
        json_schema_extra={"input_type": "password"},
    )
    schema_name: Optional[str] = Field(
        default=None,
        alias="schema",
        description="Default schema for a schema-enabled project",
    )
    quota_name: Optional[str] = Field(default=None, description="MaxCompute quota name")
    tunnel_endpoint: Optional[str] = Field(default=None, description="Optional Instance Tunnel endpoint")
    namespace_mode: Literal["auto", "two_level", "three_level"] = Field(
        default="auto",
        description="Namespace mode; auto detects whether the project supports schemas",
    )
    timeout_seconds: int = Field(default=30, gt=0, description="Connection timeout in seconds")
    query_timeout_seconds: int = Field(default=600, gt=0, description="SQL job timeout in seconds")
    default_hints: Dict[str, Any] = Field(default_factory=dict, description="Default MaxCompute SQL hints")
    ignore_table_patterns: List[str] = Field(
        default_factory=list,
        description=(
            "Glob patterns of tables and views to exclude from metadata listing, "
            "e.g. ['tmp_*', '*_bak']. Matched case-insensitively against the object "
            "name. Empty (the default) lists everything. Set it per datasource in "
            "agent.yml to drop scratch tables that jobs create and drop at will."
        ),
    )

    @field_validator("ignore_table_patterns", mode="before")
    @classmethod
    def _normalize_ignore_patterns(cls, value: Any) -> Any:
        """Accept a bare string and drop blanks.

        ``ignore_table_patterns: "tmp_*"`` is as valid as a list -- YAML users will try
        both. Blank entries are dropped rather than kept as an empty glob, which would
        silently match nothing and look like the option is broken.
        """
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple)):
            return [str(pattern).strip() for pattern in value if str(pattern).strip()]
        return value

    @field_validator("project", "endpoint")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value
