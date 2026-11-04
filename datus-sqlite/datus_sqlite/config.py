# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
"""Native SQLite configuration shared by construction and schema export."""

from pydantic import AliasChoices, Field, field_validator

from datus_db_core import ConnectionConfig


class SQLiteConfig(ConnectionConfig):
    uri: str = Field(
        validation_alias=AliasChoices("uri", "db_path"),
        description="SQLite file path, sqlite:/// URI, or :memory:",
        json_schema_extra={"input_type": "file_path"},
    )
    read_only: bool = False
    check_same_thread: bool = False
    database_name: str | None = None

    @field_validator("uri")
    @classmethod
    def nonempty_database_path(cls, value: str) -> str:
        """Require a file path or explicit :memory:, never an implicit temp DB."""
        if not value.removeprefix("sqlite:///"):
            raise ValueError("SQLite database path must not be empty; use :memory: explicitly")
        return value

    @property
    def db_path(self) -> str:
        """Return the native path while preserving the public URI input."""
        return self.uri.removeprefix("sqlite:///")
