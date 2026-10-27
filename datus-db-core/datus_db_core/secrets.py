# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Optional, per-identity Secrets Manager caching shared by native and SQLAlchemy adapters."""

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar

from datus_db_core.aws import AWSCredentialError, AWSIdentity, aws_error
from datus_db_core.exceptions import DatusDbException, ErrorCode

T = TypeVar("T")


@dataclass(frozen=True)
class SecretSnapshot(Generic[T]):
    """An atomically validated credential version; never include its value in repr."""

    value: T = field(repr=False)
    version: str
    expires_at: float


class AWSSecretProvider(Generic[T]):
    """Cache one Secret for one immutable SDK identity and adapter-specific decoder.

    Values are fetched lazily. A failed refresh never extends a prior snapshot's
    lifetime. The adapter controls when to acquire credentials and retire sessions.
    SDK imports and refreshable cloud credentials remain owned by AWSIdentity.
    """

    def __init__(
        self,
        identity: AWSIdentity,
        secret_ref: str,
        ttl_seconds: int,
        decode: Callable[[str], T],
    ):
        if not secret_ref or not 1 <= ttl_seconds <= 86400:
            raise ValueError("A Secret reference and cache TTL between 1 and 86400 seconds are required")
        self.identity = identity
        self._secret_ref = secret_ref
        self._ttl_seconds = ttl_seconds
        self._decode = decode
        self._lock = threading.RLock()
        self._snapshot: SecretSnapshot[T] | None = None

    def load(self, force: bool = False) -> SecretSnapshot[T]:
        with self._lock:
            if not force and self._snapshot is not None and time.monotonic() < self._snapshot.expires_at:
                return self._snapshot
            try:
                response = self.identity.client("secretsmanager").get_secret_value(
                    SecretId=self._secret_ref, VersionStage="AWSCURRENT"
                )
            except Exception as exc:
                safe_error = exc if isinstance(exc, AWSCredentialError) else aws_error("Database Secret retrieval", exc)
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED, message_args={"error_message": str(safe_error)}
                ) from None
            try:
                version, raw = response["VersionId"], response["SecretString"]
                if not isinstance(version, str) or not version or not isinstance(raw, str) or not raw:
                    raise ValueError("Invalid Secret response")
                value = self._decode(raw)
            except Exception:
                # A decoder may receive PEMs, passwords, JSON and SDK error text.
                # Do not expose its exception or input through logs/error chains.
                raise DatusDbException(
                    ErrorCode.DB_AUTHENTICATION_FAILED,
                    message_args={"error_message": "Invalid database Secret: check credential format and user mapping"},
                ) from None
            snapshot = SecretSnapshot(value, version, time.monotonic() + self._ttl_seconds)
            self._snapshot = snapshot
            return snapshot
