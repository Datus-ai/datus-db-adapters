from importlib.metadata import entry_points
from pathlib import Path

import pytest

from datus_bigquery.skills import get_bigquery_sql_generation_notes, get_skills_dir


def test_bigquery_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-bigquery-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_bigquery_sql_generation_notes()
    assert notes.startswith("# Google BigQuery SQL")
    for marker in ("QUALIFY", "UNNEST", "SAFE_CAST", "PARTITION BY", "NOT ENFORCED"):
        assert marker in notes
    assert "AUTO_INCREMENT" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


def test_skill_entry_point_resolves_packaged_directory():
    candidates = entry_points().select(group="datus.skills", name="bigquery")

    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()


def test_notes_return_content_without_frontmatter(tmp_path, monkeypatch):
    import datus_bigquery.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# Google BigQuery SQL\n\nbody\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_BIGQUERY_SQL_SKILL", skill_file)

    assert skills.get_bigquery_sql_generation_notes() == "# Google BigQuery SQL\n\nbody"


def test_notes_reject_unterminated_frontmatter(tmp_path, monkeypatch):
    import datus_bigquery.skills as skills

    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("---\nname: db-bigquery-sql\n", encoding="utf-8")
    monkeypatch.setattr(skills, "_BIGQUERY_SQL_SKILL", skill_file)

    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        skills.get_bigquery_sql_generation_notes()
