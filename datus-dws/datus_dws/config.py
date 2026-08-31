# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from datus_postgresql import PostgreSQLConfig

DEFAULT_DWS_PORT = 8000


def normalize_dws_endpoint(host: str, port: Any = None) -> tuple[str, int]:
    """Normalize a console endpoint into separate host and port values.

    The DWS console presents the public endpoint as ``host:port``, so accept
    that form directly rather than making the user split it by hand.
    """
    endpoint = str(host or "").strip()
    if not endpoint:
        raise ValueError("DWS host is required")
    if "://" in endpoint:
        raise ValueError("DWS host must not include a URI scheme")

    # A bare IPv6 literal has to be bracketed before urlsplit will read it as a
    # host rather than a host:port pair. Config keeps host and port in separate
    # fields, so users write the address unbracketed.
    if endpoint.count(":") > 1 and not endpoint.startswith("["):
        endpoint = f"[{endpoint}]"

    parsed = urlsplit(f"//{endpoint}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("DWS host must not contain user information")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("DWS host must contain only a hostname and optional port")
    if not parsed.hostname:
        raise ValueError("DWS host is invalid")

    try:
        embedded_port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid DWS endpoint: {endpoint}") from exc

    explicit_port = None
    if port is not None and str(port).strip():
        try:
            explicit_port = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid DWS port: {port}") from exc

    if embedded_port is not None and explicit_port is not None and embedded_port != explicit_port:
        raise ValueError(f"DWS endpoint port {embedded_port} conflicts with explicit port {explicit_port}")

    effective_port = embedded_port if embedded_port is not None else explicit_port
    effective_port = DEFAULT_DWS_PORT if effective_port is None else effective_port
    if not 1 <= effective_port <= 65535:
        raise ValueError(f"DWS port must be between 1 and 65535: {effective_port}")
    return parsed.hostname, effective_port


class DWSConfig(PostgreSQLConfig):
    """Connection configuration for Huawei Cloud GaussDB(DWS)."""

    @model_validator(mode="before")
    @classmethod
    def normalize_endpoint(cls, values):
        if not isinstance(values, dict) or "host" not in values:
            return values
        normalized = dict(values)
        host, port = normalize_dws_endpoint(normalized.get("host"), normalized.get("port"))
        normalized["host"] = host
        normalized["port"] = port
        return normalized

    host: str = Field(..., min_length=1, description="DWS coordinator endpoint, optionally with a port")
    port: int = Field(default=DEFAULT_DWS_PORT, ge=1, le=65535, description="DWS coordinator port")
    username: str = Field(..., min_length=1, description="DWS database user")
    password: str = Field(
        default="",
        repr=False,
        description="DWS password",
        json_schema_extra={"input_type": "password"},
    )
    database: str = Field(..., min_length=1, description="DWS database name (the cluster default is 'gaussdb')")
    schema_name: str = Field(default="public", alias="schema", min_length=1, description="Default schema name")
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = Field(
        default="prefer",
        description=(
            "PostgreSQL SSL mode. 'prefer' upgrades automatically when the cluster enforces SSL. "
            "'verify-full' cannot succeed against the DWS default server certificate, whose CN is "
            "'server' and which carries no subjectAltName"
        ),
    )
    sslrootcert: Optional[str] = Field(
        default=None,
        description=(
            "CA certificate for sslmode=verify-ca, as a file path or inline PEM content. "
            "Use v2/sslcert/cacert.pem from the console's dws_ssl_cert bundle; the v1 CA does "
            "not match the server certificate issuer"
        ),
    )
    timeout_seconds: int = Field(default=30, gt=0, description="Connection and pool timeout in seconds")
