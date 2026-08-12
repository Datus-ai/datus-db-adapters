"""Snowflake SQL skill discovery and Agent 0.3.9 compatibility helpers."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_SNOWFLAKE_SQL_SKILL = _SKILLS_DIR / "db-snowflake-sql" / "SKILL.md"


def get_skills_dir() -> str:
    """Return the packaged directory exposed through the ``datus.skills`` entry point."""
    return str(_SKILLS_DIR)


def get_snowflake_sql_generation_notes() -> str:
    """Return the Snowflake skill body for the Agent 0.3.9 notes hook."""
    content = _SNOWFLAKE_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()

    marker = "\n---\n"
    frontmatter_end = content.find(marker, 3)
    if frontmatter_end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_SNOWFLAKE_SQL_SKILL}")
    return content[frontmatter_end + len(marker) :].strip()
