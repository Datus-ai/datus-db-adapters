# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import ssl
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def connection_port(protocol: str, port: Optional[int]) -> int:
    """Resolve HTTP(S) defaults without guessing the protocol from a port."""
    if protocol not in ("http", "https"):
        raise ValueError("protocol must be http or https")
    return port if port is not None else (8443 if protocol == "https" else 8123)


class ClickHouseConfig(BaseModel):
    """ClickHouse-specific configuration."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    host: str = Field(default="localhost", description="ClickHouse server host")
    port: Optional[int] = Field(default=None, description="HTTP(S) port; defaults to 8123 or 8443 by protocol")
    username: str = Field(..., description="ClickHouse username")
    password: str = Field(default="", repr=False, description="ClickHouse password")
    database: Optional[str] = Field(default=None, description="Default database name")
    timeout_seconds: int = Field(default=30, gt=0, description="Pool and HTTP connect/read timeout in seconds")
    query_timeout_seconds: int | None = Field(default=None, gt=0, description="Override server max_execution_time")
    protocol: Literal["http", "https"] = "http"
    verify: bool = Field(default=True, description="Verify the HTTPS certificate chain and hostname")
    ca_cert: Optional[str] = Field(default=None, description="Optional PEM CA bundle file for verified HTTPS")

    @model_validator(mode="after")
    def validate_tls(self):
        self.port = connection_port(self.protocol, self.port)
        if self.ca_cert is not None:
            if self.protocol != "https" or not self.verify:
                raise ValueError("ca_cert requires protocol=https and verify=true")
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
