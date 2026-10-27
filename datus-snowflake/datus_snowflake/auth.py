# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Snowflake credential decoding; authentication and credential storage stay separate."""

import json
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization

from datus_db_core.aws import AWSIdentity
from datus_db_core.secrets import AWSSecretProvider

from .config import SnowflakeConfig


def private_key_to_der(private_key: str, private_key_file_pwd: str | None = None) -> bytes:
    """Convert a PEM key, retaining existing static-profile handling of escaped newlines."""
    normalized_pem = private_key.strip()
    if "\\n" in normalized_pem and "\n" not in normalized_pem:
        normalized_pem = normalized_pem.replace("\\n", "\n")
    passphrase = private_key_file_pwd.encode() if private_key_file_pwd else None
    try:
        key = serialization.load_pem_private_key(normalized_pem.encode(), password=passphrase)
    except TypeError:
        if passphrase is None:
            raise
        key = serialization.load_pem_private_key(normalized_pem.encode(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@dataclass(frozen=True)
class SnowflakeSecretCredentials:
    username: str
    password: str | None = field(default=None, repr=False)
    private_key: bytes | None = field(default=None, repr=False)

    def connection_parameters(self) -> dict:
        if self.private_key is not None:
            return {"user": self.username, "authenticator": "SNOWFLAKE_JWT", "private_key": self.private_key}
        return {"user": self.username, "password": self.password}


class SnowflakeCredentials:
    """A Secret source bound to one connector identity, with no process-global cache."""

    def __init__(self, config: SnowflakeConfig):
        self.config = config.model_copy(deep=True)
        self.identity = AWSIdentity(self.config.aws)
        self._username: str | None = self.config.username
        self.provider = AWSSecretProvider(
            self.identity, self.config.secret_ref, self.config.secret_cache_ttl_seconds, self._decode
        )

    def _decode(self, raw: str) -> SnowflakeSecretCredentials:
        mapping = self.config.secret_key_mapping
        keys = mapping.model_dump(exclude_none=True) if mapping else {}
        credential_key = "private_key" if self.config.auth_mode == "key_pair" else "password"
        if keys:
            source = json.loads(raw)
            if not isinstance(source, dict):
                raise ValueError("Secret mapping requires a JSON object")
            values = {target: source[key] for target, key in keys.items()}
        else:
            values = {credential_key: raw}
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise ValueError("Mapped credentials must be nonempty strings")
        username = values.get("username", self.config.username)
        if not username or self._username is not None and username != self._username:
            # Changing a user also changes permissions and cached metadata. Make
            # the owner replace the connector instead of silently changing identity.
            raise ValueError("Secret username change requires a new connector")
        if self.config.auth_mode == "key_pair":
            result = SnowflakeSecretCredentials(
                username, private_key=private_key_to_der(values["private_key"], values.get("private_key_file_pwd"))
            )
        else:
            result = SnowflakeSecretCredentials(username, password=values["password"])
        self._username = username
        return result
