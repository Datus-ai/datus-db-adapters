# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Packaged Athena SQL skill discovery and SQL-generation notes."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_ATHENA_SQL_SKILL = _SKILLS_DIR / "db-athena-sql" / "SKILL.md"


def get_skills_dir() -> str:
    """Return the directory exposed through the ``datus.skills`` entry point."""
    return str(_SKILLS_DIR)


def get_athena_sql_generation_notes() -> str:
    """Return the maintained skill body without YAML frontmatter."""
    content = _ATHENA_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()

    marker = "\n---\n"
    frontmatter_end = content.find(marker, 3)
    if frontmatter_end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_ATHENA_SQL_SKILL}")
    return content[frontmatter_end + len(marker) :].strip()
