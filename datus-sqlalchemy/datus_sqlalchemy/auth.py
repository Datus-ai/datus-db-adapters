# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Relational database authentication configuration and per-connector credentials."""

import json
import re
import threading
import time
from typing import Any, Callable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from datus_db_core import DatusDbException, ErrorCode
from datus_db_core.aws import AWSCredentialError, AWSIdentity, AWSIdentityConfig, aws_error


class DatabaseAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    username: str
    password: str = Field(default="", repr=False, json_schema_extra={"input_type": "password"})
    aurora: bool = Field(default=False, description="Require verified TLS for an Aurora endpoint")
    auth_mode: Literal["password", "iam"] = "password"
    aws: AWSIdentityConfig | None = None
    secret_ref: str | None = Field(default=None, min_length=1, description="Secrets Manager secret name or ARN")
    secret_cache_ttl_seconds: int = Field(default=300, ge=1, le=86400)
    connect_timeout_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_authentication(self) -> Self:
        if self.auth_mode == "iam" and (self.password or self.secret_ref):
            raise ValueError("IAM authentication cannot be combined with password or secret_ref")
        if self.secret_ref and self.password:
            raise ValueError("secret_ref cannot be combined with an inline password")
        return self


class DatabaseCredentials:
    """A dynamic credential source bound to a connector's identity/user/endpoint.

    The configuration is snapshotted: change identities by replacing the connector
    and closing its old pools. Secret versions can change within the same identity.
    Expired cache refresh failures prevent new checkouts; checked-out work continues.
    """

    def __init__(self, config):
        self.config = config.model_copy(deep=True)
        self.identity = AWSIdentity(config.aws)
        self._lock = threading.RLock()
        self._expires_at = 0.0
        self._password: str | None = None
        self._version: str | None = None

    def secret_version(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._password is not None and time.monotonic() < self._expires_at:
                return self._version
            try:
                result = self.identity.client("secretsmanager").get_secret_value(
                    SecretId=self.config.secret_ref, VersionStage="AWSCURRENT"
                )
                value = json.loads(result["SecretString"])
                if not isinstance(value, dict) or not isinstance(value.get("password"), str):
                    raise ValueError("Secret must contain a JSON password string")
                if "username" in value and value["username"] != self.config.username:
                    raise ValueError("Secret username does not match the configured database user")
                version = result["VersionId"]
                if not isinstance(version, str) or not version:
                    raise ValueError("Secret version is missing")
            except (ValueError, KeyError, TypeError):
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED,
                    message_args={
                        "error_message": "Invalid database Secret: expected password, matching username and VersionId"
                    },
                ) from None
            except Exception as exc:
                safe_error = exc if isinstance(exc, AWSCredentialError) else aws_error("Database Secret retrieval", exc)
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED, message_args={"error_message": str(safe_error)}
                ) from None
            self._password = value["password"]
            self._version = version
            self._expires_at = time.monotonic() + self.config.secret_cache_ttl_seconds
            return version

    def connection_parameters(self) -> dict:
        if self.config.auth_mode == "iam":
            try:
                password = self.identity.database_token(self.config.host, self.config.port, self.config.username)
            except AWSCredentialError as exc:
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED, message_args={"error_message": str(exc)}
                ) from None
        elif self.config.secret_ref:
            with self._lock:
                self.secret_version()
                password = self._password
        else:
            password = self.config.password
        return {"user": self.config.username, "password": password}


def aurora_writer_validator(config: DatabaseAuthConfig, dialect: str) -> Callable[[Any], None] | None:
    """Reject a former writer reached through a stale Aurora cluster DNS record.

    AWS cluster writer endpoints have a distinct documented hostname form.
    Reader endpoints can legitimately reach the writer when no replicas exist;
    instance/custom endpoints do not promise a writer role either.
    """
    host = getattr(config, "host", "").lower().rstrip(".")
    if not config.aurora or dialect not in {"mysql", "postgresql"}:
        return None
    if not re.fullmatch(r"[^.]+\.cluster-[a-z0-9]+\.[a-z0-9-]+\.rds\.amazonaws\.com(?:\.cn)?", host):
        return None

    def validate(connection: Any) -> None:
        from sqlalchemy.exc import DisconnectionError

        cursor = connection.cursor()
        try:
            cursor.execute("SELECT @@innodb_read_only" if dialect == "mysql" else "SELECT pg_is_in_recovery()")
            read_only = bool(cursor.fetchone()[0])
        finally:
            cursor.close()
            # A checkout has no application transaction. End only the probe's
            # transaction so the caller retains its normal isolation behavior.
            connection.rollback()
        if read_only:
            raise DisconnectionError("Aurora writer endpoint resolved to a reader; establish a new connection")

    return validate


def configure_database_connection(
    connector: Any, config: DatabaseAuthConfig, connect_args: dict[str, Any], *, dialect: str
) -> None:
    """Install static TLS/timeout parameters and optional per-physical-connect auth."""
    timeout = config.connect_timeout_seconds or config.timeout_seconds
    if timeout > 0:
        connect_args["connect_timeout"] = timeout
    # PostgreSQL-family dialects own TLS and timeout translation. Passing libpq
    # options directly as DBAPI kwargs would overwrite materialized CA paths
    # and bypass pg8000's SSLContext / timeout mapping.
    url_defaults = None
    if dialect == "postgresql":
        url_defaults = {key: str(value) for key, value in connect_args.items()}
        connect_args = {}
    dynamic = config.aurora or config.auth_mode == "iam" or config.secret_ref is not None
    credentials = DatabaseCredentials(config) if dynamic else None
    connector.configure_connection(
        connect_args=connect_args,
        url_defaults=url_defaults,
        connection_parameters=credentials.connection_parameters if credentials else None,
        credential_version=credentials.secret_version if credentials and config.secret_ref else None,
        connection_cleanup=credentials.identity.close if credentials else None,
        connection_validator=aurora_writer_validator(config, dialect),
    )
