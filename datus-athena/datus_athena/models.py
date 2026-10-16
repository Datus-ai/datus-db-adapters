# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Athena diagnostics without changing other adapters' result contract."""

from typing import Any

from pydantic import Field

from datus_db_core import ExecuteSQLResult


class AthenaExecuteSQLResult(ExecuteSQLResult):
    query_execution_id: str | None = None
    execution_info: dict[str, Any] = Field(default_factory=dict)
