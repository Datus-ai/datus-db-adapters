# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Optional per-dialect SQL operations that adapters can register.

Adapters whose SQL surface differs from the common path (e.g. Oracle has no
``LIMIT`` clause and no multi-row ``INSERT ... VALUES``) implement this
protocol and pass an instance to ``connector_registry.register`` via the
``dialect_operations`` keyword. Callers dispatch on capability presence:

    operations = connector_registry.get_dialect_operations(dialect)
    if operations is not None:
        sql = operations.render_limit(base_sql, limit)
    else:
        sql = f"{base_sql} LIMIT {limit}"  # existing path, byte-identical

Adapters that do not register an instance keep the caller's existing SQL
generation untouched.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datus_db_core.base import BaseSqlConnector


@runtime_checkable
class DialectOperations(Protocol):
    """Dialect-specific SQL rendering and data-transfer operations."""

    def render_limit(self, sql: str, limit: int) -> str:
        """Return ``sql`` restricted to at most ``limit`` rows.

        ``sql`` is a complete SELECT statement without any row-limiting
        clause. The default (non-registered) path appends ``LIMIT {n}``.
        """
        ...

    def render_count(self, sql: str, alias: str) -> str:
        """Return a statement counting the rows produced by ``sql``.

        ``alias`` is the required subquery alias. The default path renders
        ``SELECT COUNT(*) AS __datus_count FROM ({sql}) AS {alias}``; dialects
        that reject ``AS`` before a table alias (Oracle) re-render it.
        """
        ...

    def quote_identifier(self, name: str) -> str:
        """Return ``name`` quoted for safe use as a column identifier.

        Implementations own the dialect's case-folding contract: the quoted
        form must stay reachable from unquoted SQL written later (e.g. Oracle
        upper-cases the name before double-quoting).
        """
        ...

    def infer_transfer_type(self, series: Any) -> str:
        """Return the dialect column type for a pandas ``series``.

        Used when auto-creating a transfer target table from a source result
        set.
        """
        ...

    def write_dataframe(
        self,
        connector: "BaseSqlConnector",
        table: str,
        dataframe: Any,
        batch_size: int,
    ) -> int:
        """Write ``dataframe`` into ``table`` and return the row count.

        Implementations own statement construction (bound parameters,
        ``executemany``), value coercion (e.g. bool -> 1/0) and the final
        commit. ``table`` may be schema-qualified and is passed through as-is.
        """
        ...
