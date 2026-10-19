# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

import re
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

from datus_oracle.skills import get_oracle_sql_generation_notes, get_skills_dir


def test_oracle_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-oracle-sql" / "SKILL.md"

    assert skill_file.is_file()
    references = re.findall(r"\]\((references/[^)]+)\)", skill_file.read_text())
    assert references
    for reference in references:
        resource = files("datus_oracle").joinpath("skills", "db-oracle-sql", reference)
        assert resource.is_file() and resource.read_text().strip()
    notes = get_oracle_sql_generation_notes()
    reference_body = (skill_file.parent / "references/plsql.md").read_text()
    assert notes.startswith("# Oracle SQL")
    assert "FETCH FIRST n ROWS ONLY" in notes
    assert ":id" in notes
    assert "Oracle 19c has no SQL `BOOLEAN`" in notes
    assert "SYS_REFCURSOR" in reference_body
    assert "function can return directly" in reference_body
    assert "`OUT` or `IN OUT` parameter" in reference_body
    assert "consumed later with `FETCH`" in reference_body
    assert "`RETURNING INTO` receives DML outputs" in reference_body
    assert "AUTHID CURRENT_USER" in reference_body
    assert "`WHEN OTHERS` suppresses the original failure" in reference_body
    assert not notes.startswith("---")


def test_oracle_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="oracle")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
