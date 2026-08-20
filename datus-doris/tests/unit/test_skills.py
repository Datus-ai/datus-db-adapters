# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

import pytest

from datus_db_core import connector_registry
from datus_doris import register
from datus_doris.skills import get_doris_sql_generation_notes, get_skills_dir


def test_doris_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-doris-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_doris_sql_generation_notes()
    assert notes.startswith("# Apache Doris SQL")
    assert "DUPLICATE KEY" in notes
    # The three loading paths the skill documents, each with a runnable example.
    assert "_stream_load" in notes
    assert "INSERT INTO SELECT" in notes
    assert "CREATE ROUTINE LOAD" in notes
    # Broker Load was deliberately dropped in favour of TVF-based file import.
    assert "LOAD LABEL" not in notes
    assert "WITH S3|HDFS|BROKER" not in notes
    # Both materialized view kinds, plus the state check that gates rewrite.
    assert "CREATE MATERIALIZED VIEW" in notes
    assert "SHOW CREATE MATERIALIZED VIEW sync_agg_mv ON app_log" in notes
    assert "mv_infos(" in notes
    assert "MaterializedViewRewriteSuccessAndChose" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


def test_doris_registration_and_skill_entry_point():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()
        notes = connector_registry.get_sql_generation_notes("doris")
        candidates = entry_points().select(group="datus.skills", name="doris")

        assert callable(notes)
        assert notes() == get_doris_sql_generation_notes()
        assert len(candidates) == 1
        assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


def test_notes_return_content_without_frontmatter_unchanged(tmp_path, monkeypatch):
    """A skill file that carries no frontmatter is returned as-is."""
    import datus_doris.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Apache Doris SQL\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_DORIS_SQL_SKILL", skill_file)

    assert skills.get_doris_sql_generation_notes() == "# Apache Doris SQL\n\nbody"


def test_notes_reject_unterminated_frontmatter(tmp_path, monkeypatch):
    import datus_doris.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: db-doris-sql\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_DORIS_SQL_SKILL", skill_file)

    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        skills.get_doris_sql_generation_notes()
