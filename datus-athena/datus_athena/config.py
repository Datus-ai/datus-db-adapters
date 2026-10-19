# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Connection and bounded execution settings for AWS Athena."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from datus_db_core import ConnectionConfig


class AthenaConfig(ConnectionConfig):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    region: str | None = Field(default=None, description="AWS region; otherwise use the SDK configuration")
    profile_name: str | None = Field(default=None, description="AWS profile, including refreshable role profiles")
    ca_bundle: str | None = Field(
        default=None, description="Optional trusted CA bundle; TLS verification stays enabled"
    )
    workgroup: str = Field(default="primary", min_length=1)
    catalog: str = Field(default="AwsDataCatalog", min_length=1)
    database: str | None = None
    output_location: str | None = Field(default=None, description="Optional s3:// result prefix")
    timeout_seconds: float = Field(default=30, gt=0, description="SDK connection and socket read timeout")
    query_timeout_seconds: float = Field(default=600, gt=0)
    result_timeout_seconds: float = Field(default=60, gt=0)
    cancel_timeout_seconds: float = Field(default=10, ge=0)
    poll_interval_seconds: float = Field(default=1, gt=0)
    max_rows: int = Field(default=10000, gt=0)
    max_result_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    page_size: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def normalize_aws_identity(cls, value):
        """Normalize the shared AWS identity shape to the adapter's flat settings.

        Only configuration names are normalized here. Credential acquisition and
        refresh remain entirely in the SDK's providers.
        """
        if not isinstance(value, dict) or "aws" not in value:
            return value
        normalized = dict(value)
        identity = normalized.pop("aws")
        if isinstance(identity, BaseModel):
            identity = identity.model_dump(exclude_none=True)
        if not isinstance(identity, dict):
            raise ValueError("aws must be an identity configuration mapping")
        fields = {"region": "region", "profile": "profile_name", "ca_bundle": "ca_bundle"}
        if set(identity) - fields.keys():
            raise ValueError("aws accepts only region, profile, and ca_bundle; use SDK credential providers")
        for source, target in fields.items():
            if source not in identity or identity[source] is None:
                continue
            if target in normalized and normalized[target] is not None and normalized[target] != identity[source]:
                raise ValueError(f"Conflicting {target} and aws.{source} settings")
            normalized[target] = identity[source]
        return normalized

    @field_validator("region", "profile_name", "ca_bundle", "database", "output_location", mode="before")
    @classmethod
    def clean_optional_string(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("workgroup", "catalog")
    @classmethod
    def clean_required_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("output_location")
    @classmethod
    def validate_output_location(cls, value: str | None) -> str | None:
        if value:
            from urllib.parse import urlsplit

            parsed = urlsplit(value)
            if (
                parsed.scheme != "s3"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "output_location must be an s3://bucket/prefix without credentials or query parameters"
                )
        return value
