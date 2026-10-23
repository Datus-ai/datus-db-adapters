# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Any, Dict, Literal, Mapping, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _extract_prefixed_config(carrier_map: Mapping[str, Any], prefix: str) -> Dict[str, Any]:
    """Extract Hive configuration from a prefixed carrier map."""
    hive_config: Dict[str, Any] = {}
    prefix_len = len(prefix)

    for key, value in carrier_map.items():
        if key.startswith(prefix):
            hive_key = key[prefix_len:]
            hive_config[hive_key] = value

    configuration_params: Dict[str, Any] = {}
    base_params: Dict[str, Any] = {}

    for key, value in hive_config.items():
        if key.startswith("configuration."):
            config_key = key[14:]
            configuration_params[config_key] = value
        else:
            if key == "port" and isinstance(value, str) and value.isdigit():
                base_params[key] = int(value)
            else:
                base_params[key] = value

    result = base_params.copy()
    result["configuration"] = configuration_params
    return result


class HiveConfig(BaseModel):
    """Hive-specific configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="Hive server host")
    port: int = Field(default=10000, description="Hive server port")
    server_type: Literal["hive", "inceptor"] = Field(
        default="hive", description="Server result protocol: Apache Hive or Transwarp Inceptor"
    )
    database: Optional[str] = Field(default=None, description="Default database name")
    username: str = Field(..., description="Hive username")
    password: str = Field(
        default="",
        description="Hive password",
        json_schema_extra={"input_type": "password"},
    )
    auth: Optional[str] = Field(
        default=None,
        description="Authentication mechanism (NONE, LDAP, CUSTOM, KERBEROS)",
    )
    kerberos_service_name: Optional[str] = Field(
        default=None, description="Kerberos service component of the server principal (usually hive)"
    )
    configuration: Dict[str, Any] = Field(default_factory=dict, description="Hive session configuration")
    timeout_seconds: int = Field(default=30, gt=0, description="Pool and Thrift connect/read/write timeout in seconds")
    query_timeout_seconds: int | None = Field(default=None, gt=0, description="Override Thrift statement queryTimeout")

    @model_validator(mode="after")
    def validate_kerberos(self) -> Self:
        if self.auth == "KERBEROS":
            if not self.kerberos_service_name or not self.kerberos_service_name.strip():
                raise ValueError("kerberos_service_name is required when auth is KERBEROS")
            self.kerberos_service_name = self.kerberos_service_name.strip()
            if self.password:
                raise ValueError("KERBEROS uses a ticket cache obtained with kinit; do not supply password")
        elif self.kerberos_service_name is not None:
            raise ValueError("kerberos_service_name requires auth=KERBEROS")
        return self

    @classmethod
    def from_config_map(cls, config_map: Mapping[str, Any], prefix: str) -> "HiveConfig":
        """Build HiveConfig from a prefixed carrier map."""
        extracted = _extract_prefixed_config(config_map, prefix)
        return cls(**extracted)
