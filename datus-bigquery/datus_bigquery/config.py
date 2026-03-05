# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BigQueryConfig(BaseModel):
    """BigQuery-specific configuration."""

    model_config = ConfigDict(extra="forbid")

    project: str = Field(..., description="GCP project ID (used as catalog)")
    dataset: Optional[str] = Field(default=None, description="Default dataset name (used as database)")
    credentials_path: Optional[str] = Field(default=None, description="Path to service account JSON file")
    location: Optional[str] = Field(default=None, description="Dataset location (e.g., US, EU)")
    timeout_seconds: int = Field(default=60, description="Query timeout in seconds")
