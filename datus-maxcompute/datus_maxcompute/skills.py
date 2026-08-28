# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""MaxCompute SQL skill discovery and legacy SQL-notes compatibility helpers."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_MAXCOMPUTE_SQL_SKILL = _SKILLS_DIR / "db-maxcompute-sql" / "SKILL.md"


def get_skills_dir() -> str:
    """Return the packaged directory exposed through the ``datus.skills`` entry point."""
    return str(_SKILLS_DIR)


def get_maxcompute_sql_generation_notes() -> str:
    """Return the MaxCompute skill body for the legacy SQL-notes hook."""
    content = _MAXCOMPUTE_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()

    marker = "\n---\n"
    frontmatter_end = content.find(marker, 3)
    if frontmatter_end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_MAXCOMPUTE_SQL_SKILL}")
    return content[frontmatter_end + len(marker) :].strip()
