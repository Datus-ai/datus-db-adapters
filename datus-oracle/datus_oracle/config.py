# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OracleConfig(BaseModel):
    """Oracle-specific configuration.

    Exactly one connection target must be provided: ``service_name``
    (recommended, addresses a PDB/service), ``sid`` (legacy databases) or
    ``dsn`` (TNS alias / full connect descriptor). ``database`` is accepted
    as a compatibility alias for ``service_name``; the service/PDB is a
    connection target only and never appears in SQL object identifiers.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    host: str = Field(default="127.0.0.1", description="Oracle server host")
    port: int = Field(default=1521, description="Oracle listener port")
    username: str = Field(..., description="Oracle username")
    password: str = Field(
        default="",
        description="Oracle password",
        json_schema_extra={"input_type": "password"},
    )
    service_name: Optional[str] = Field(default=None, description="Oracle service name (PDB), recommended")
    sid: Optional[str] = Field(default=None, description="Oracle SID (legacy environments)")
    dsn: Optional[str] = Field(default=None, description="TNS alias or full connect descriptor")
    schema_name: Optional[str] = Field(
        default=None,
        alias="schema",
        description="Default schema (object namespace); defaults to the connecting user's schema",
    )
    timeout_seconds: int = Field(default=30, description="Connection timeout in seconds")

    @model_validator(mode="before")
    @classmethod
    def _accept_database_alias(cls, data):
        if isinstance(data, dict) and data.get("database"):
            data = dict(data)
            database = data.pop("database")
            if not data.get("service_name"):
                data["service_name"] = database
        return data

    @model_validator(mode="after")
    def _validate_connection_target(self):
        targets = [name for name in ("service_name", "sid", "dsn") if getattr(self, name)]
        if len(targets) > 1:
            raise ValueError(f"service_name, sid and dsn are mutually exclusive, got: {', '.join(targets)}")
        if not targets:
            raise ValueError("One of service_name, sid or dsn is required")
        return self
