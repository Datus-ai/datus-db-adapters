"""Static HS2 data and comparator checks never connect to a database."""

from copy import deepcopy
from pathlib import Path

import pytest

from datus_db_core.testing.hs2 import compare, render, validate

ROOT = Path(__file__).resolve().parents[1] / "fixtures/hs2"


def test_vendored_hs2_fixture_is_valid_and_complete():
    native, metrics = validate(ROOT)
    assert native and metrics
    assert any(case["id"] == "date.to_date_mode" for case in native)
    assert any(case["id"] == "metrics.date_boundaries" for case in metrics)


def test_exact_comparison_rejects_precision_loss_and_keeps_padding():
    expected = {
        "columns": ["n", "s"],
        "rows": [["9007199254740993", "a "]],
        "types": ["decimal", "text"],
        "ordered": True,
    }
    compare(expected, ["n", "s"], [["9007199254740993.0", "a "]])
    for actual in [[9007199254740992.0, "a "], ["9007199254740993", "a"]]:
        with pytest.raises(AssertionError):
            compare(expected, ["n", "s"], [actual])


def test_comparison_retains_duplicates_null_and_empty():
    expected = {"columns": ["s"], "rows": [[None], [""]], "types": ["text"], "ordered": False}
    compare(expected, ["s"], [[""], [None]])
    with pytest.raises(AssertionError):
        compare(expected, ["s"], [[None], [None]])
    ordered = deepcopy(expected)
    ordered["ordered"] = True
    with pytest.raises(AssertionError):
        compare(ordered, ["s"], [[""], [None]])


def test_fixture_templates_reject_unknown_or_unsafe_identifiers():
    for sql, database in [("SELECT {{missing}}", "owned"), ("SELECT {{db}}", "bad-name")]:
        with pytest.raises(AssertionError):
            render(sql, database)
