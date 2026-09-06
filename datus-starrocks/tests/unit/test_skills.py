# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from importlib.metadata import entry_points
from pathlib import Path

from datus_db_core import connector_registry
from datus_starrocks import register
from datus_starrocks.skills import get_skills_dir, get_starrocks_sql_generation_notes


def test_starrocks_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-starrocks-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_starrocks_sql_generation_notes()
    _, separator, body = skill_file.read_text(encoding="utf-8").partition("\n---\n")
    assert separator == "\n---\n"
    assert notes == body.strip()
    assert notes.startswith("# StarRocks SQL")
    assert not notes.startswith("---")


def test_starrocks_registration_and_skill_entry_point():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()
        notes = connector_registry.get_sql_generation_notes("starrocks")
        candidates = entry_points().select(group="datus.skills", name="starrocks")

        assert callable(notes)
        assert notes() == get_starrocks_sql_generation_notes()
        assert len(candidates) == 1
        assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
