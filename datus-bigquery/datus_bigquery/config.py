# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, Secret, SecretStr, field_validator, model_validator


class BigQueryConfig(BaseModel):
    """BigQuery connection configuration.

    Datus calls the two namespace levels ``catalog`` and ``database`` while
    BigQuery calls them ``project`` and ``dataset``. Both spellings are
    accepted so datasource configuration and per-request context agree.
    """

    model_config = ConfigDict(extra="forbid")

    project: str = Field(..., description="GCP project ID (Datus catalog)")
    dataset: Optional[str] = Field(default=None, description="Default dataset (Datus database)")
    credentials_path: Optional[str] = Field(default=None, description="Path to a service-account JSON file")
    credentials_info: Optional[Secret[dict[str, Any]]] = Field(
        default=None,
        description="Service-account JSON object",
        json_schema_extra={"input_type": "password", "value_type": "json_object"},
    )
    credentials_base64: Optional[SecretStr] = Field(
        default=None,
        description="Base64-encoded service-account JSON",
        json_schema_extra={"input_type": "password"},
    )
    billing_project_id: Optional[str] = Field(default=None, description="Optional billing/quota project")
    location: Optional[str] = Field(default=None, description="BigQuery job location, for example US or EU")
    timeout_seconds: int = Field(default=60, gt=0, description="Datus operation timeout in seconds")

    @model_validator(mode="before")
    @classmethod
    def normalize_datus_namespace_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        catalog = normalized.pop("catalog", None)
        database = normalized.pop("database", None)
        if catalog is not None:
            normalized["project"] = catalog
        if database is not None:
            normalized["dataset"] = database
        return normalized

    @field_validator("project")
    @classmethod
    def validate_project(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("project must not be empty")
        return value

    @field_validator(
        "dataset",
        "credentials_path",
        "billing_project_id",
        "location",
        mode="before",
    )
    @classmethod
    def normalize_optional_strings(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @model_validator(mode="after")
    def validate_credentials(self):
        configured = [self.credentials_path, self.credentials_info, self.credentials_base64]
        if sum(item is not None for item in configured) > 1:
            raise ValueError("Configure only one of credentials_path, credentials_info, or credentials_base64")
        return self
