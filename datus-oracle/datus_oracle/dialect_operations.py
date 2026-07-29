# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Oracle implementation of the datus-db-core DialectOperations protocol."""

from typing import Any

from datus_db_core import get_logger

from .type_mapping import infer_oracle_type

logger = get_logger(__name__)


def quote_oracle_identifier(name: str) -> str:
    """Quote ``name`` as an upper-cased Oracle identifier.

    Oracle folds unquoted identifiers to upper case while quoted identifiers
    are case sensitive. Upper-casing before quoting keeps quoted identifiers
    (safe for reserved words) reachable from unquoted SQL written later.
    """
    escaped = str(name).replace('"', "")
    return f'"{escaped.upper()}"'


class OracleDialectOperations:
    """Dialect-specific SQL rendering and data-transfer operations for Oracle."""

    def render_limit(self, sql: str, limit: int) -> str:
        return f"{sql} FETCH FIRST {int(limit)} ROWS ONLY"

    def render_count(self, sql: str, alias: str) -> str:
        # Oracle rejects AS before a table alias (ORA-00933)
        return f"SELECT COUNT(*) AS __datus_count FROM ({sql}) {alias}"

    def quote_identifier(self, name: str) -> str:
        return quote_oracle_identifier(name)

    def infer_transfer_type(self, series: Any) -> str:
        return infer_oracle_type(series)

    def write_dataframe(
        self,
        connector: Any,
        table: str,
        dataframe: Any,
        batch_size: int,
    ) -> int:
        """Write ``dataframe`` into ``table`` with bound parameters.

        Oracle 19c does not support multi-row ``INSERT ... VALUES (...), (...)``
        or TRUE/FALSE literals, so rows are written via ``executemany`` with
        named binds and booleans coerced to 1/0.
        """
        from sqlalchemy import text

        columns = list(dataframe.columns)
        col_names = ", ".join(quote_oracle_identifier(c) for c in columns)
        bind_names = ", ".join(f":p{i}" for i in range(len(columns)))
        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({bind_names})"

        row_count = len(dataframe)
        rows_written = 0
        with connector._conn() as conn:
            for batch_start in range(0, row_count, batch_size):
                batch = dataframe.iloc[batch_start : batch_start + batch_size]
                params = [
                    {f"p{i}": _coerce_bind_value(value) for i, value in enumerate(row)}
                    for row in batch.itertuples(index=False, name=None)
                ]
                conn.execute(text(insert_sql), params)
                rows_written += len(batch)
            conn.commit()
        logger.debug(f"Wrote {rows_written} rows into {table}")
        return rows_written


def _coerce_bind_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    return value
