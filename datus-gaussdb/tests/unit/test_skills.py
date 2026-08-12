# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

import pytest

from datus_gaussdb.skills import get_gaussdb_sql_generation_notes, get_skills_dir


@pytest.mark.acceptance
def test_gaussdb_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-gaussdb-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_gaussdb_sql_generation_notes()
    assert notes.startswith("# GaussDB SQL")
    assert "IS NULL" in notes
    assert "DISTRIBUTE BY HASH" in notes
    assert "compatibility mode" in notes
    assert "ON DUPLICATE KEY UPDATE" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


@pytest.mark.acceptance
def test_gaussdb_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="gaussdb")
    if not candidates:
        pytest.skip("datus-gaussdb is not installed as a distribution (source-tree run)")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
