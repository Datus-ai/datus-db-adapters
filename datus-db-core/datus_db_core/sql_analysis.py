# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Optional adapter-owned SQL analysis, independent of execution SDKs."""

from dataclasses import dataclass
from typing import Literal

from .constants import SQLType


@dataclass(frozen=True)
class SQLAnalysis:
    """Only complete analysis may supply facts to object-scope checks.

    Table names are SQL identifiers, with quoting preserved. An empty tuple
    means no physical tables only when ``status == 'complete'``.
    """

    status: Literal["complete", "unsupported", "error", "incomplete"]
    sql_type: SQLType = SQLType.UNKNOWN
    tables: tuple[str, ...] = ()
    diagnostic: str = ""

    def require_complete(self) -> "SQLAnalysis":
        if self.status != "complete":
            raise ValueError(f"SQL analysis {self.status}: {self.diagnostic}")
        return self
