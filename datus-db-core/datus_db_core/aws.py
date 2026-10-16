# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Optional AWS identity support shared by adapters; importing this module needs no SDK."""

import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from datus_db_core.logging import get_logger

logger = get_logger(__name__)


class AWSIdentityConfig(BaseModel):
    """Use the SDK credential chain, including refreshable role/web-identity profiles.

    Configure role_arn/source_profile (or credential_source) in the AWS shared
    config, rather than copying an STS response into an adapter configuration.
    ca_bundle applies to AWS HTTPS APIs, not to database TLS connections.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: str | None = Field(default=None, min_length=1)
    profile: str | None = Field(default=None, min_length=1)
    ca_bundle: str | None = Field(default=None, min_length=1)


class AWSCredentialError(RuntimeError):
    """An AWS failure whose message contains no SDK response or credential value."""


def aws_error(operation: str, exc: Exception) -> AWSCredentialError:
    """Retain a diagnostic code without exposing response messages or signed URLs."""
    response = getattr(exc, "response", {})
    code = (
        response.get("Error", {}).get("Code", type(exc).__name__) if isinstance(response, dict) else type(exc).__name__
    )
    safe_code = "".join(c for c in str(code) if c.isalnum() or c in "_-.")[:80]
    return AWSCredentialError(f"{operation} failed ({safe_code})")


class AWSIdentity:
    """Own an SDK Session and clients for one immutable identity configuration.

    No credentials are extracted/frozen. SDK providers keep refreshing credentials
    for the same identity. Each adapter instance owns its own identity and caches.
    """

    def __init__(
        self,
        config: AWSIdentityConfig | None = None,
        *,
        session: Any = None,
        client_config: Any = None,
    ):
        """Use fixed SDK client options for this identity's cached clients.

        ``client_config`` accepts a botocore Config without importing botocore
        for ordinary adapters. An injected Session retains its credential
        providers; no credentials are copied from it.
        """
        self.config = config or AWSIdentityConfig()
        self._lock = threading.Lock()
        self._session: Any = session
        self._client_config = client_config
        self._clients: dict[str, Any] = {}

    def client(self, service: str):
        with self._lock:
            if service in self._clients:
                return self._clients[service]
            try:
                if self._session is None:
                    try:
                        import boto3
                    except ImportError:
                        raise AWSCredentialError("AWS authentication requires the adapter's [aws] extra") from None
                    self._session = boto3.Session(profile_name=self.config.profile, region_name=self.config.region)
                options = {"verify": self.config.ca_bundle} if self.config.ca_bundle else {}
                if self._client_config is not None:
                    options["config"] = self._client_config
                client = self._session.client(service, **options)
            except AWSCredentialError:
                raise
            except Exception as exc:
                raise aws_error("AWS client initialization", exc) from None
            self._clients[service] = client
            return client

    def close(self) -> None:
        """Release all cached clients between operations; allow later reopening.

        The Session is retained so its credential providers and refresh state
        survive. Detach the cache under the lock and attempt every client's
        cleanup even when an individual client raises.
        """
        with self._lock:
            clients, self._clients = self._clients, {}
        for service, client in clients.items():
            try:
                client.close()
            except Exception as exc:
                logger.warning("AWS client cleanup failed for %s (%s)", service, type(exc).__name__)

    def database_token(self, host: str, port: int, username: str) -> str:
        try:
            client = self.client("rds")
            return client.generate_db_auth_token(DBHostname=host, Port=port, DBUsername=username)
        except AWSCredentialError:
            raise
        except Exception as exc:
            raise aws_error("IAM database token generation", exc) from None
