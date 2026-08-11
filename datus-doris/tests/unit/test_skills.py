# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

from datus_db_core import connector_registry
from datus_doris import register
from datus_doris.skills import get_doris_sql_generation_notes, get_skills_dir


def test_doris_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-doris-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_doris_sql_generation_notes()
    assert notes.startswith("# Apache Doris SQL")
    assert "DUPLICATE KEY" in notes
    assert "LOAD LABEL" in notes
    assert "Stream Load through the HTTP API" in notes
    assert "without imposing a polling workflow" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


def test_doris_registration_and_skill_entry_point():
    register()
    notes = connector_registry.get_sql_generation_notes("doris")
    candidates = entry_points().select(group="datus.skills", name="doris")

    assert callable(notes)
    assert notes() == get_doris_sql_generation_notes()
    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
