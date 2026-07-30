# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, model_validator

from datus_postgresql import PostgreSQLConfig


def normalize_hologres_endpoint(host: str, port: Any = None) -> tuple[str, int]:
    """Normalize a console endpoint into separate host and port values."""
    endpoint = str(host or "").strip()
    if not endpoint:
        raise ValueError("Hologres host is required")
    if "://" in endpoint:
        raise ValueError("Hologres host must not include a URI scheme")

    parsed = urlsplit(f"//{endpoint}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Hologres host must not contain user information")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Hologres host must contain only a hostname and optional port")
    if not parsed.hostname:
        raise ValueError("Hologres host is invalid")

    try:
        embedded_port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid Hologres endpoint: {endpoint}") from exc

    explicit_port = None
    if port is not None and str(port).strip():
        try:
            explicit_port = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Hologres port: {port}") from exc

    if embedded_port is not None and explicit_port is not None and embedded_port != explicit_port:
        raise ValueError(f"Hologres endpoint port {embedded_port} conflicts with explicit port {explicit_port}")

    effective_port = embedded_port if embedded_port is not None else explicit_port
    effective_port = 80 if effective_port is None else effective_port
    if not 1 <= effective_port <= 65535:
        raise ValueError(f"Hologres port must be between 1 and 65535: {effective_port}")
    return parsed.hostname, effective_port


class HologresConfig(PostgreSQLConfig):
    """Connection configuration for Alibaba Cloud Hologres."""

    @model_validator(mode="before")
    @classmethod
    def normalize_endpoint(cls, values):
        if not isinstance(values, dict) or "host" not in values:
            return values
        normalized = dict(values)
        host, port = normalize_hologres_endpoint(normalized.get("host"), normalized.get("port"))
        normalized["host"] = host
        normalized["port"] = port
        return normalized

    host: str = Field(..., min_length=1, description="Hologres endpoint hostname, optionally with a port")
    port: int = Field(default=80, ge=1, le=65535, description="Hologres endpoint port")
    username: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("username", "access_key_id"),
        description="Alibaba Cloud AccessKey ID",
    )
    password: str = Field(
        ...,
        repr=False,
        validation_alias=AliasChoices("password", "access_key_secret"),
        description="Alibaba Cloud AccessKey Secret",
        json_schema_extra={"input_type": "password"},
    )
    database: str = Field(..., min_length=1, description="Hologres database name")
    schema_name: str = Field(default="public", alias="schema", min_length=1, description="Default schema name")
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = Field(
        default="prefer",
        description="PostgreSQL SSL mode",
    )
    timeout_seconds: int = Field(default=30, gt=0, description="Connection and pool timeout in seconds")
