# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from datus_db_core.aws import AWSIdentityConfig


class SnowflakeSecretMapping(BaseModel):
    """Top-level JSON keys supplying credentials; absent mapping means raw text."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)
    private_key: str | None = Field(default=None, min_length=1)
    private_key_file_pwd: str | None = Field(default=None, min_length=1)

    @field_validator("username", "password", "private_key", "private_key_file_pwd", mode="before")
    @classmethod
    def empty_mapping_is_unset(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class SnowflakeConfig(BaseModel):
    """Snowflake-specific configuration."""

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, coerce_numbers_to_str=True, hide_input_in_errors=True
    )

    account: str = Field(..., description="Snowflake account identifier")
    username: str | None = Field(default=None, min_length=1, description="Snowflake username, or map from the Secret")
    auth_mode: Literal["password", "key_pair"] | None = Field(
        default=None, description="Required with secret_ref; static profiles retain their existing credential selection"
    )
    secret_ref: str | None = Field(default=None, min_length=1, description="Secrets Manager Secret name or full ARN")
    secret_key_mapping: SnowflakeSecretMapping | None = None
    secret_cache_ttl_seconds: int = Field(default=300, ge=1, le=300)
    aws: AWSIdentityConfig | None = None
    password: Optional[SecretStr] = Field(
        default=None,
        description="Snowflake password (omit when using key pair authentication)",
        json_schema_extra={"input_type": "password"},
    )
    private_key_file: Optional[str] = Field(
        default=None,
        description="Path to PEM-encoded RSA private key for key pair authentication (SNOWFLAKE_JWT)",
    )
    private_key: Optional[SecretStr] = Field(
        default=None,
        description=(
            "PEM-encoded RSA private key for key pair authentication (SNOWFLAKE_JWT); "
            "takes precedence over private_key_file and password"
        ),
        json_schema_extra={"input_type": "password"},
    )
    private_key_file_pwd: Optional[SecretStr] = Field(
        default=None,
        description="Passphrase for the encrypted private key file (omit for unencrypted keys)",
        json_schema_extra={"input_type": "password"},
    )
    warehouse: str = Field(..., description="Snowflake warehouse name")
    database: Optional[str] = Field(default=None, description="Default database name")
    schema_name: Optional[str] = Field(default=None, alias="schema", description="Default schema name")
    role: Optional[str] = Field(default=None, description="Snowflake role to use")
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")

    @staticmethod
    def _has_secret(value: Optional[SecretStr]) -> bool:
        return value is not None and bool(value.get_secret_value())

    @model_validator(mode="after")
    def _require_supported_credential(self) -> "SnowflakeConfig":
        mapping = self.secret_key_mapping
        mapped = mapping.model_dump(exclude_none=True) if mapping else {}
        if self.secret_ref:
            if self.auth_mode is None:
                raise ValueError("secret_ref requires auth_mode=password or key_pair")
            if (
                any(self._has_secret(v) for v in (self.password, self.private_key, self.private_key_file_pwd))
                or self.private_key_file
            ):
                raise ValueError(
                    "secret_ref cannot be combined with inline password, private key, key file or passphrase"
                )
            credential_key = "private_key" if self.auth_mode == "key_pair" else "password"
            allowed = {"username", credential_key}
            if self.auth_mode == "key_pair":
                allowed.add("private_key_file_pwd")
            if mapped and (credential_key not in mapped or set(mapped) - allowed):
                raise ValueError("Secret mapping must supply the selected authentication credential only")
            if not self.username and "username" not in mapped:
                raise ValueError("Provide username or a Secret username mapping")
            return self
        if mapped or self.aws is not None:
            raise ValueError("Secret mapping and AWS identity require secret_ref")
        if not self.username:
            raise ValueError("Snowflake username is required")
        if self.auth_mode is not None:
            if self.auth_mode == "password" and (self.private_key_file or self._has_secret(self.private_key)):
                raise ValueError("auth_mode=password cannot be combined with key-pair credentials")
            if self.auth_mode == "key_pair" and not (self.private_key_file or self._has_secret(self.private_key)):
                raise ValueError("auth_mode=key_pair requires private key credentials")
        has_private_key = self._has_secret(self.private_key)
        if has_private_key:
            return self

        has_password = self._has_secret(self.password)
        has_key = bool(self.private_key_file)
        if has_password == has_key:
            raise ValueError(
                "SnowflakeConfig requires `private_key`, or exactly one of `password` "
                "or `private_key_file` (private_key takes precedence when provided)"
            )
        return self
