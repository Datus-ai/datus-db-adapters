# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

import pytest

from datus_maxcompute.skills import get_maxcompute_sql_generation_notes, get_skills_dir


def test_maxcompute_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-maxcompute-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_maxcompute_sql_generation_notes()
    assert notes.startswith("# MaxCompute SQL")
    assert not notes.startswith("---")
    assert "TODO" not in notes
    assert len(notes) < 5_000


def test_skill_covers_maxcompute_sql_correctness_and_cost_boundaries():
    notes = get_maxcompute_sql_generation_notes()

    for rule in (
        "project.table",
        "project.schema.table",
        "odps.sql.allow.fullscan",
        "LIMIT",
        "positional",
        "Transactional or Delta",
        "parser dialect",
    ):
        assert rule in notes, f"{rule!r} missing from the skill"


def test_maxcompute_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="maxcompute")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()


def test_notes_return_content_without_frontmatter_unchanged(tmp_path, monkeypatch):
    import datus_maxcompute.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# MaxCompute SQL\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_MAXCOMPUTE_SQL_SKILL", skill_file)

    assert skills.get_maxcompute_sql_generation_notes() == "# MaxCompute SQL\n\nbody"


def test_notes_reject_unterminated_frontmatter(tmp_path, monkeypatch):
    import datus_maxcompute.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: db-maxcompute-sql\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_MAXCOMPUTE_SQL_SKILL", skill_file)

    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        skills.get_maxcompute_sql_generation_notes()
