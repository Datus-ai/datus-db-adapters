"""Packaged Hive SQL guidance and optional Inceptor reference."""

from pathlib import Path

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"
_HIVE_SQL_SKILL = _SKILLS_DIR / "db-hive-sql" / "SKILL.md"


def get_skills_dir() -> str:
    return str(_SKILLS_DIR)


def get_hive_sql_generation_notes() -> str:
    """Expose the same entrypoint to hosts using the legacy notes hook."""
    content = _HIVE_SQL_SKILL.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return content.strip()
    end = content.find("\n---\n", 3)
    if end < 0:
        raise ValueError(f"Invalid skill frontmatter: {_HIVE_SQL_SKILL}")
    return content[end + len("\n---\n") :].strip()
