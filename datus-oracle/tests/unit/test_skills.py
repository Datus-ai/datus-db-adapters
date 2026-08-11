# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

from importlib.metadata import entry_points
from pathlib import Path

from datus_oracle.skills import get_oracle_sql_generation_notes, get_skills_dir


def test_oracle_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-oracle-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_oracle_sql_generation_notes()
    assert notes.startswith("# Oracle SQL")
    assert "FETCH FIRST n ROWS ONLY" in notes
    assert "Oracle 19c has no SQL `BOOLEAN`" in notes
    assert "SYS_REFCURSOR" in notes
    assert "AUTHID CURRENT_USER" in notes
    assert "`WHEN OTHERS` suppresses the original failure" in notes
    assert not notes.startswith("---")


def test_oracle_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="oracle")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
