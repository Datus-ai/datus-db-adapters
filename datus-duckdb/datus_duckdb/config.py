# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
"""The actual DuckDB configuration used for both construction and schema export."""

from typing import Any

from pydantic import AliasChoices, Field

from datus_db_core import ConnectionConfig


class DuckDBConfig(ConnectionConfig):
    uri: str = Field(
        validation_alias=AliasChoices("uri", "db_path"),
        description="DuckDB file path, duckdb:/// URI, or :memory:",
        json_schema_extra={"input_type": "file_path"},
    )
    read_only: bool = Field(default=False, description="Open an existing database without writes")
    enable_external_access: bool = Field(default=True, description="Enable external file access")
    memory_limit: str | None = Field(default=None, description="DuckDB memory limit, e.g. 2GB")
    database_name: str | None = Field(default=None, description="Optional database name override")
    iceberg: dict[str, Any] | None = Field(default=None, description="DuckDB Iceberg REST catalog configuration")

    @property
    def db_path(self) -> str:
        """Keep the existing connector API without a second configuration model."""
        return self.uri.removeprefix("duckdb:///")
