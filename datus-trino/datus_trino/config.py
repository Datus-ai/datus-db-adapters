# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import ssl
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TrinoConfig(BaseModel):
    """Trino-specific configuration."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    host: str = Field(default="127.0.0.1", description="Trino server host")
    port: int = Field(default=8080, ge=1, le=65535, description="Trino server port")
    username: str = Field(..., min_length=1, description="Trino username")
    password: str = Field(
        default="",
        repr=False,
        description="Trino password",
        json_schema_extra={"input_type": "password"},
    )
    catalog: str = Field(default="hive", description="Default catalog name")
    schema_name: str = Field(default="default", description="Default schema name")
    http_scheme: Literal["http", "https"] = Field(default="http", description="HTTP scheme (http or https)")
    verify: bool = Field(default=True, description="Verify the HTTPS certificate chain and hostname")
    ca_cert: str | None = Field(default=None, description="Optional PEM CA bundle file for verified HTTPS")
    timeout_seconds: int = Field(default=30, gt=0, description="Connection timeout in seconds")
    query_timeout_seconds: int | None = Field(default=None, gt=0, description="Override the server query run deadline")

    @model_validator(mode="after")
    def validate_tls(self):
        if self.ca_cert is not None:
            if self.http_scheme != "https" or not self.verify:
                raise ValueError("ca_cert requires http_scheme=https and verify=true")
            if not self.ca_cert.strip():
                raise ValueError("ca_cert must reference a readable PEM CA bundle file")
            try:
                path = Path(self.ca_cert).expanduser().resolve()
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(cafile=str(path))
            except (OSError, ValueError):
                raise ValueError("ca_cert must reference a readable PEM CA bundle file") from None
            self.ca_cert = str(path)
        return self
