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


def test_notes_route_mode_sensitive_sql_by_database_compatibility():
    notes = get_dws_sql_generation_notes()

    assert "SELECT datcompatibility" in notes
    assert "current_database()" in notes
    assert "Never infer it from `server_version`" in notes
    assert "### ORA mode" in notes
    assert "### TD mode" in notes
    assert "### MySQL mode" in notes


def test_notes_cover_td_silent_coercions_and_guc_dependent_behaviour():
    notes = get_dws_sql_generation_notes()

    for rule in (
        "`'' IS NULL` is false",
        "`''::int` yields `0`",
        "`varchar + int` as `numeric + numeric`",
        "td_compatible_truncation",
        "convert_empty_str_to_null_td",
        "strict_text_concat_td",
        "bpchar_text_without_rtrim",
    ):
        assert rule in notes


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
