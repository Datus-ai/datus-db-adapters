# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

import pytest

from datus_db_core import connector_registry
from datus_tidb import register
from datus_tidb.skills import get_skills_dir, get_tidb_sql_generation_notes


def test_tidb_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-tidb-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_tidb_sql_generation_notes()
    assert notes.startswith("# TiDB SQL")
    assert not notes.startswith("---")
    assert "TODO" not in notes


def test_skill_names_the_constructs_tidb_rejects():
    notes = get_tidb_sql_generation_notes()

    for construct in ("FULL OUTER JOIN", "JSON_TABLE", "LATERAL", "CREATE TABLE ... AS SELECT", "CORR"):
        assert construct in notes, f"{construct!r} missing from the skill"


def test_skill_warns_about_the_clauses_tidb_accepts_but_ignores():
    """These raise no error, which is exactly why the model has to be told."""
    notes = get_tidb_sql_generation_notes()

    assert "tidb_enable_check_constraint" in notes
    assert "silently" in notes.lower()


def test_skill_lists_every_window_function_that_reaches_tiflash_mpp():
    """Only these seven push down; the rest fall back to single-node execution
    on the TiDB layer (live-verified on TiDB v8.5 + TiFlash)."""
    notes = get_tidb_sql_generation_notes()

    for function in ("ROW_NUMBER", "RANK", "DENSE_RANK", "LEAD", "LAG", "FIRST_VALUE", "LAST_VALUE"):
        assert function in notes
    assert "TIFLASH_REPLICA" in notes
    assert "read_from_storage" in notes
    # The actionable rewrite: GROUP BY aggregation does push down, its window
    # equivalent does not.
    assert "GROUP BY" in notes


def test_skill_documents_the_tidb_explain_shape():
    """TiDB's estimate column is estRows, not MySQL's rows."""
    assert "estRows" in get_tidb_sql_generation_notes()


def test_tidb_registration_and_skill_entry_point():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()
        notes = connector_registry.get_sql_generation_notes("tidb")
        candidates = entry_points().select(group="datus.skills", name="tidb")

        assert callable(notes)
        assert notes() == get_tidb_sql_generation_notes()
        assert len(candidates) == 1
        assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


def test_notes_return_content_without_frontmatter_unchanged(tmp_path, monkeypatch):
    """A skill file that carries no frontmatter is returned as-is."""
    import datus_tidb.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# TiDB SQL\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_TIDB_SQL_SKILL", skill_file)

    assert skills.get_tidb_sql_generation_notes() == "# TiDB SQL\n\nbody"


def test_notes_reject_unterminated_frontmatter(tmp_path, monkeypatch):
    import datus_tidb.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: db-tidb-sql\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_TIDB_SQL_SKILL", skill_file)

    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        skills.get_tidb_sql_generation_notes()
