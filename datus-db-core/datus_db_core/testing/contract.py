# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Shared DB adapter contract assertions.

These helpers keep integration tests focused on adapter-specific SQL while
enforcing the same observable Datus-facing behavior across engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

RowAssert = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class TableContractCase:
    """Contract case for adapters that can create a temporary table."""

    adapter_name: str
    table_name: str
    drop_sql: str
    create_sql: str
    insert_sqls: Sequence[str]
    qualified_select_sql: str
    limit_sql: str
    schema_kwargs: Mapping[str, Any]
    expected_columns: Sequence[str]
    limit_count: int = 1
    row_assert: RowAssert | None = None
    dialect_select_sqls: Sequence[str] = field(default_factory=tuple)
    cleanup_sqls: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class SelectContractCase:
    """Contract case for read-only adapters or read-only catalogs."""

    adapter_name: str
    select_sql: str
    limit_sql: str
    qualified_sql: str
    limit_count: int = 1
    row_assert: RowAssert | None = None
    dialect_select_sqls: Sequence[str] = field(default_factory=tuple)


def assert_success(result: Any, operation: str) -> None:
    """Assert an ExecuteSQLResult-like object succeeded."""

    assert result.success, f"{operation} failed: {getattr(result, 'error', None)}"


def assert_error_result(result: Any, operation: str) -> None:
    """Assert an ExecuteSQLResult-like object failed with a useful error."""

    assert not result.success, f"{operation} unexpectedly succeeded: {getattr(result, 'sql_return', None)}"
    assert result.error, f"{operation} failed without an error message"


def assert_default_contract_row(row: Mapping[str, Any]) -> None:
    """Validate stable aliases returned by contract SELECT queries."""

    assert row["id_value"] == 1
    assert row["mixed_value"] == "Alpha"
    assert row["special_value"] == "S-1"
    assert row["nullable_value"] is None
    assert str(row["event_date_value"]).startswith("2024-02-03")
    event_ts_text = str(row["event_ts_value"])
    assert "2024-02-03" in event_ts_text
    assert ":" in event_ts_text, f"Expected timestamp-like value, got {event_ts_text!r}"
    assert Decimal(str(row["amount_value"])) == Decimal("123.45")
    assert str(row["bool_value"]).lower() in {"true", "1"}


def assert_schema_columns(schema: Iterable[Mapping[str, Any]], expected_columns: Sequence[str]) -> None:
    """Assert schema metadata includes expected columns case-insensitively."""

    actual = {str(column.get("name", "")).lower() for column in schema}
    missing = [column for column in expected_columns if column.lower() not in actual]
    assert not missing, f"Missing schema columns {missing}; actual columns: {sorted(actual)}"


def _assert_payload(result: Any, operation: str) -> Sequence[Any]:
    """Return a non-null result payload with a clear assertion message."""

    payload = result.sql_return
    assert payload is not None, f"{operation} returned no payload"
    return payload


def _cleanup_table_contract(connector: Any, case: TableContractCase, contract_error: BaseException | None) -> None:
    cleanup_errors = []
    for cleanup_sql in (*case.cleanup_sqls, case.drop_sql):
        cleanup_result = connector.execute_ddl(cleanup_sql)
        if not cleanup_result.success:
            cleanup_errors.append(f"{cleanup_sql!r}: {getattr(cleanup_result, 'error', None)}")

    if not cleanup_errors:
        return

    message = f"{case.adapter_name} contract cleanup failed: {'; '.join(cleanup_errors)}"
    if contract_error is not None:
        contract_error.add_note(message)
        return
    raise AssertionError(message)


def assert_table_contract(connector: Any, case: TableContractCase) -> None:
    """Run the shared table contract against a live adapter connector."""

    connector.execute_ddl(case.drop_sql)
    contract_error: BaseException | None = None
    try:
        assert_success(
            connector.execute_ddl(case.create_sql),
            f"{case.adapter_name} create contract table",
        )
        for index, insert_sql in enumerate(case.insert_sqls, start=1):
            assert_success(
                connector.execute_insert(insert_sql),
                f"{case.adapter_name} insert contract row {index}",
            )

        schema = connector.get_schema(**dict(case.schema_kwargs, table_name=case.table_name))
        assert_schema_columns(schema, case.expected_columns)

        definitions = connector.get_tables_with_ddl(**dict(case.schema_kwargs, tables=[case.table_name]))
        assert [entry["table_name"].casefold() for entry in definitions] == [case.table_name.casefold()]
        definition = definitions[0]["definition"]
        assert definition.strip() and not definition.startswith("-- DDL not available"), definition
        for column in case.expected_columns:
            assert column.casefold() in definition.casefold(), f"{case.adapter_name} DDL omitted column {column!r}"

        result = connector.execute({"sql_query": case.qualified_select_sql}, result_format="list")
        assert_success(result, f"{case.adapter_name} qualified contract SELECT")
        rows = _assert_payload(result, f"{case.adapter_name} qualified contract SELECT")
        assert rows, f"{case.adapter_name} qualified contract SELECT returned no rows"
        (case.row_assert or assert_default_contract_row)(rows[0])

        limited = connector.execute({"sql_query": case.limit_sql}, result_format="list")
        assert_success(limited, f"{case.adapter_name} LIMIT contract SELECT")
        limited_rows = _assert_payload(limited, f"{case.adapter_name} LIMIT contract SELECT")
        assert len(limited_rows) == case.limit_count

        for index, dialect_sql in enumerate(case.dialect_select_sqls, start=1):
            dialect_result = connector.execute({"sql_query": dialect_sql}, result_format="list")
            assert_success(dialect_result, f"{case.adapter_name} dialect-specific contract SELECT {index}")
            dialect_rows = _assert_payload(
                dialect_result,
                f"{case.adapter_name} dialect-specific contract SELECT {index}",
            )
            assert dialect_rows, f"{case.adapter_name} dialect-specific contract SELECT {index} returned no rows"
    except BaseException as error:
        contract_error = error
        raise
    finally:
        _cleanup_table_contract(connector, case, contract_error)


def assert_select_contract(connector: Any, case: SelectContractCase) -> None:
    """Run the shared read-only contract against a live adapter connector."""

    result = connector.execute({"sql_query": case.select_sql}, result_format="list")
    assert_success(result, f"{case.adapter_name} typed SELECT contract")
    rows = _assert_payload(result, f"{case.adapter_name} typed SELECT contract")
    assert rows, f"{case.adapter_name} typed SELECT contract returned no rows"
    (case.row_assert or assert_default_contract_row)(rows[0])

    limited = connector.execute({"sql_query": case.limit_sql}, result_format="list")
    assert_success(limited, f"{case.adapter_name} LIMIT contract SELECT")
    limited_rows = _assert_payload(limited, f"{case.adapter_name} LIMIT contract SELECT")
    assert len(limited_rows) == case.limit_count

    qualified = connector.execute({"sql_query": case.qualified_sql}, result_format="list")
    assert_success(qualified, f"{case.adapter_name} qualified identifier SELECT")
    qualified_rows = _assert_payload(qualified, f"{case.adapter_name} qualified identifier SELECT")
    assert qualified_rows, f"{case.adapter_name} qualified identifier SELECT returned no rows"

    for index, dialect_sql in enumerate(case.dialect_select_sqls, start=1):
        dialect_result = connector.execute({"sql_query": dialect_sql}, result_format="list")
        assert_success(dialect_result, f"{case.adapter_name} dialect-specific contract SELECT {index}")
        dialect_rows = _assert_payload(
            dialect_result,
            f"{case.adapter_name} dialect-specific contract SELECT {index}",
        )
        assert dialect_rows, f"{case.adapter_name} dialect-specific contract SELECT {index} returned no rows"
