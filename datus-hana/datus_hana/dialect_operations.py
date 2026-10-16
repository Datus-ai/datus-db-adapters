# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from .handlers import quote_identifier


class HanaDialectOperations:
    def render_limit(self, sql: str, limit: int) -> str:
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        return f"{sql} LIMIT {int(limit)}"

    def render_count(self, sql: str, alias: str) -> str:
        return f"SELECT COUNT(*) AS __datus_count FROM ({sql}) AS {quote_identifier(alias)}"

    def quote_identifier(self, name: str) -> str:
        return quote_identifier(name)

    def infer_transfer_type(self, series) -> str:
        raise NotImplementedError("HANA migration target support is not declared")

    def write_dataframe(self, connector, table, dataframe, batch_size):
        raise NotImplementedError("HANA migration target support is not declared")
