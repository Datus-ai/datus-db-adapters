import json
import re
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import pytest

from datus_db_core import connector_registry
from datus_hana import register
from datus_hana.handlers import parse_hana_identifier
from datus_hana.skills import get_hana_sql_generation_notes, get_skills_dir


def test_hana_skill_is_packaged_and_preserves_metadata_identifiers():
    resource = files("datus_hana").joinpath("skills", "db-hana-sql", "SKILL.md")
    content = resource.read_text(encoding="utf-8")
    assert "name: db-hana-sql" in content
    _, separator, body = content.partition("\n---\n")
    assert separator == "\n---\n"
    notes = get_hana_sql_generation_notes()
    assert notes == body.strip()
    assert notes.startswith("# SAP HANA SQL")
    example = re.search(r"```json\n(.*?)\n```", notes, re.DOTALL)
    assert example is not None
    arguments = json.loads(example.group(1))
    parts = parse_hana_identifier(arguments["table_name"])
    assert parts["schema_name"] == arguments["schema_name"] == "APP"
    assert parts["table_name"] == "orders"


def test_hana_registration_and_skill_entry_point():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()
        assert connector_registry.get_sql_generation_notes("hana") is get_hana_sql_generation_notes
        candidates = list(entry_points(group="datus.skills", name="hana"))
        assert len(candidates) == 1
        assert candidates[0].load() is get_skills_dir
        assert Path(candidates[0].load()()).resolve() == Path(get_skills_dir()).resolve()
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)


def test_notes_without_frontmatter(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    skill.write_text("# SAP HANA SQL\n\nPreserve quoted identifiers.\n", encoding="utf-8")
    monkeypatch.setattr("datus_hana.skills._HANA_SQL_SKILL", skill)
    assert get_hana_sql_generation_notes() == "# SAP HANA SQL\n\nPreserve quoted identifiers."


def test_notes_reject_malformed_frontmatter(tmp_path, monkeypatch):
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: db-hana-sql\n", encoding="utf-8")
    monkeypatch.setattr("datus_hana.skills._HANA_SQL_SKILL", skill)
    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        get_hana_sql_generation_notes()
