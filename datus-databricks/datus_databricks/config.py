# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

AuthMode = Literal["oauth_profile", "access_token"]


class DatabricksConfig(BaseModel):
    """Databricks SQL warehouse connection configuration.

    Datus ``catalog`` is a Unity Catalog catalog. Datus ``database`` is the
    Unity Catalog schema. OAuth tokens are not stored here: ``oauth_profile``
    names a Databricks CLI profile whose token cache lives outside this package.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    host: str = Field(..., description="Workspace hostname, without a scheme")
    warehouse_id: Optional[str] = Field(default=None, description="SQL warehouse id")
    http_path: Optional[str] = Field(default=None, description="SQL warehouse HTTP path")
    catalog: str = Field(..., description="Unity Catalog catalog (Datus catalog)")
    schema_name: str = Field(
        ...,
        alias="schema",
        description="Unity Catalog schema (Datus database)",
    )
    auth_mode: AuthMode = Field(
        default="oauth_profile",
        description="oauth_profile uses a Databricks CLI profile; access_token uses a personal access token",
    )
    profile: Optional[str] = Field(default=None, description="Databricks CLI profile name")
    access_token: Optional[SecretStr] = Field(
        default=None,
        description="Databricks personal access token",
        json_schema_extra={"input_type": "password"},
    )
    timeout_seconds: int = Field(default=120, gt=0, description="Datus operation timeout in seconds")

    @model_validator(mode="before")
    @classmethod
    def normalize_namespace_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        database = normalized.pop("database", None)
        if database is not None:
            normalized["schema"] = database
        return normalized

    @field_validator("host")
    @classmethod
    def normalize_host(cls, value: str) -> str:
        text = str(value).strip()
        if "://" in text:
            parsed = urlsplit(text)
            text = parsed.netloc or parsed.path
        text = text.split("/")[0].strip()
        if text.endswith(":443"):
            text = text[: -len(":443")]
        if not text:
            raise ValueError("host must not be empty")
        return text

    @field_validator("catalog", "schema_name", "warehouse_id", "http_path", "profile", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def validate_target_and_auth(self):
        if not self.catalog:
            raise ValueError("catalog must not be empty")
        if not self.schema_name:
            raise ValueError("schema must not be empty")
        self._validate_warehouse()
        self._validate_auth()
        return self

    def _validate_warehouse(self) -> None:
        derived = f"/sql/1.0/warehouses/{self.warehouse_id}" if self.warehouse_id else ""
        if self.http_path and not self.http_path.startswith("/"):
            raise ValueError("http_path must start with /")
        if self.http_path and derived and self.http_path != derived:
            raise ValueError("http_path does not match warehouse_id")
        if not self.http_path and not self.warehouse_id:
            raise ValueError("warehouse_id or http_path is required")

    def _validate_auth(self) -> None:
        if self.auth_mode == "oauth_profile":
            if not self.profile:
                raise ValueError("profile is required when auth_mode is oauth_profile")
            if self.access_token is not None:
                raise ValueError("Configure only one of profile or access_token")
            return
        if self.access_token is None:
            raise ValueError("access_token is required when auth_mode is access_token")
        if self.profile:
            raise ValueError("Configure only one of profile or access_token")

    def resolved_http_path(self) -> str:
        if self.http_path:
            return self.http_path
        return f"/sql/1.0/warehouses/{self.warehouse_id}"
