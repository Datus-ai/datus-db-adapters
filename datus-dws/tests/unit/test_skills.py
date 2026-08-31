# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

from pathlib import Path

from datus_dws.skills import get_dws_sql_generation_notes, get_skills_dir


def test_skills_dir_contains_the_dws_skill():
    skill = Path(get_skills_dir()) / "db-dws-sql" / "SKILL.md"

    assert skill.is_file()


def test_notes_strip_frontmatter():
    notes = get_dws_sql_generation_notes()

    assert notes.startswith("# DWS SQL")
    assert "name: db-dws-sql" not in notes


def test_notes_warn_about_the_two_silent_ora_traps():
    notes = get_dws_sql_generation_notes()

    # Both produce wrong results without raising, so the skill must call them
    # out explicitly rather than leaving them to be inferred.
    assert "7/2" in notes
    assert "not integer division" in notes.lower()
    assert "'' IS NULL" in notes


def test_notes_cover_distribution_and_portability():
    notes = get_dws_sql_generation_notes()

    for clause in ("DISTRIBUTE BY HASH", "DISTRIBUTE BY REPLICATION", "DISTRIBUTE BY ROUNDROBIN"):
        assert clause in notes
    assert "TO GROUP" in notes
    assert "TABLESPACE" in notes


def test_notes_state_the_materialized_view_gate():
    notes = get_dws_sql_generation_notes()

    assert "8.2.1.220" in notes
    assert "enable_matview" in notes
