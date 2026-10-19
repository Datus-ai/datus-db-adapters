# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

import re
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

from datus_hologres.skills import get_hologres_sql_generation_notes, get_skills_dir


def test_hologres_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-hologres-sql" / "SKILL.md"

    assert skill_file.is_file()
    references = re.findall(r"\]\((references/[^)]+)\)", skill_file.read_text())
    assert references
    for reference in references:
        resource = files("datus_hologres").joinpath("skills", "db-hologres-sql", reference)
        assert resource.is_file() and resource.read_text().strip()
    notes = get_hologres_sql_generation_notes()
    reference_body = (skill_file.parent / "references/table-design-and-writes.md").read_text()
    assert notes.startswith("# Hologres SQL")
    assert "PostgreSQL 11" in notes
    assert "distribution_key" in notes
    assert "event_time_column` columns `NOT NULL" in reference_body
    assert "external_database.schema.table" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


def test_hologres_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="hologres")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
