# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
"""Native SQLite configuration shared by construction and schema export."""

from pydantic import AliasChoices, Field

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

    @property
    def db_path(self) -> str:
        return self.uri.removeprefix("sqlite:///")
