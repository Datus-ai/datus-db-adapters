# Copyright 2025-present DatusAI, Inc.
# Licensed under the Apache License, Version 2.0.
# See http://www.apache.org/licenses/LICENSE-2.0 for details.

"""Oracle SQL skill discovery and Agent 0.3.9 compatibility helpers."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_ORACLE_SQL_SKILL = _SKILLS_DIR / "db-oracle-sql" / "SKILL.md"


def get_skills_dir() -> str:
    """Return the packaged directory exposed through the ``datus.skills`` entry point."""
    return str(_SKILLS_DIR)


def get_oracle_sql_generation_notes() -> str:
    """Return the Oracle skill body for the Agent 0.3.9 notes hook.

    SKILL.md remains the only maintained SQL guidance source. New Agent
    versions discover it through ``datus.skills``; Agent 0.3.9 receives the
    same body through ``sql_generation_notes``.
    """
    content = _ORACLE_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()

    marker = "\n---\n"
    frontmatter_end = content.find(marker, 3)
    if frontmatter_end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_ORACLE_SQL_SKILL}")
    return content[frontmatter_end + len(marker) :].strip()
