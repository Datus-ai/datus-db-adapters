# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.

"""Hologres SQL skill discovery and Agent 0.3.9 compatibility helpers."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_HOLOGRES_SQL_SKILL = _SKILLS_DIR / "db-hologres-sql" / "SKILL.md"


def get_skills_dir() -> str:
    """Return the packaged directory exposed through the ``datus.skills`` entry point."""
    return str(_SKILLS_DIR)


def get_hologres_sql_generation_notes() -> str:
    """Return the Hologres skill body for the Agent 0.3.9 notes hook."""
    content = _HOLOGRES_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()

    marker = "\n---\n"
    frontmatter_end = content.find(marker, 3)
    if frontmatter_end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_HOLOGRES_SQL_SKILL}")
    return content[frontmatter_end + len(marker) :].strip()
