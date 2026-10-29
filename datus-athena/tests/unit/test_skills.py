import json
import re
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import pytest

from datus_athena.handlers import get_athena_sql_generation_notes, parse_athena_identifier
from datus_athena.skills import get_skills_dir


def test_athena_skill_is_packaged_and_notes_share_its_body():
    resource = files("datus_athena").joinpath("skills", "db-athena-sql", "SKILL.md")
    content = resource.read_text(encoding="utf-8")
    assert "name: db-athena-sql" in content
    _, separator, body = content.partition("\n---\n")
    assert separator == "\n---\n"
    assert get_athena_sql_generation_notes() == body.strip()
    assert body.strip().startswith("# Amazon Athena SQL")

    references = re.findall(r"\]\((references/[^)]+)\)", content)
    assert references
    for reference in references:
        packaged = resource.parent.joinpath(reference)
        assert packaged.is_file() and packaged.read_text(encoding="utf-8").strip()

    example = re.search(r"```json\n(.*?)\n```", body, re.DOTALL)
    assert example is not None
    arguments = json.loads(example.group(1))
    parsed = parse_athena_identifier(arguments["table_name"])
    assert parsed == {
        "catalog_name": arguments["catalog"],
        "database_name": arguments["database"],
        "schema_name": arguments["schema_name"],
        "table_name": "orders",
    }


def test_skill_entry_point_resolves_packaged_directory():
    candidates = list(entry_points(group="datus.skills", name="athena"))
    assert len(candidates) == 1
    assert candidates[0].load() is get_skills_dir
    assert Path(candidates[0].load()()).resolve() == Path(get_skills_dir()).resolve()


def test_notes_without_frontmatter(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Amazon Athena SQL\n\nUse Trino expressions.\n", encoding="utf-8")
    monkeypatch.setattr("datus_athena.skills._ATHENA_SQL_SKILL", skill)
    assert get_athena_sql_generation_notes() == "# Amazon Athena SQL\n\nUse Trino expressions."


def test_notes_reject_malformed_frontmatter(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: db-athena-sql\n", encoding="utf-8")
    monkeypatch.setattr("datus_athena.skills._ATHENA_SQL_SKILL", skill)
    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        get_athena_sql_generation_notes()
