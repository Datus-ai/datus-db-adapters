import re
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from datus_db_core import connector_registry
from datus_hive import HiveConfig, HiveConnector, register
from datus_hive.skills import get_hive_sql_generation_notes, get_skills_dir


@pytest.mark.parametrize("server_type", ["hive", "inceptor"])
def test_sql_context_uses_configured_product_without_connecting(server_type, monkeypatch):
    connector = HiveConnector(HiveConfig(username="hive", server_type=server_type))
    monkeypatch.setattr(connector, "connect", lambda: pytest.fail("SQL context must not open a connection"))
    try:
        assert connector.get_sql_generation_context(database_name="other") == {"server_type": server_type}
        assert connector.get_type() == "hive"
    finally:
        connector.close()


def test_skill_bundle_and_registration():
    root = Path(get_skills_dir())
    skill = root / "db-hive-sql/SKILL.md"
    text = skill.read_text()
    metadata = yaml.safe_load(text.split("---", 2)[1])
    assert metadata["name"] == "db-hive-sql"
    references = re.findall(r"\]\((references/[^)]+)\)", text)
    assert references == ["references/inceptor.md"]
    for reference in references:
        resource = files("datus_hive").joinpath("skills/db-hive-sql", reference)
        assert resource.is_file() and resource.read_text().strip()
    assert get_hive_sql_generation_notes() == text.split("---\n", 2)[2].strip()
    candidates = entry_points().select(group="datus.skills", name="hive")
    assert len(candidates) == 1
    assert Path(next(iter(candidates)).load()()).resolve() == root.resolve()
    attributes = ("_connectors", "_factories", "_metadata", "_capabilities", "_uri_builders", "_context_resolvers")
    saved = {name: dict(getattr(connector_registry, name)) for name in attributes}
    try:
        register()
        assert connector_registry.get_sql_generation_notes("hive") is get_hive_sql_generation_notes
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, name)
            target.clear()
            target.update(values)


@pytest.mark.parametrize("content,expected", [("# Notes\n", "# Notes"), ("---\nname: x\n---\nbody\n", "body")])
def test_notes_strip_optional_frontmatter(tmp_path, monkeypatch, content, expected):
    import datus_hive.skills as skills

    path = tmp_path / "SKILL.md"
    path.write_text(content)
    monkeypatch.setattr(skills, "_HIVE_SQL_SKILL", path)
    assert skills.get_hive_sql_generation_notes() == expected


def test_notes_reject_incomplete_frontmatter(tmp_path, monkeypatch):
    import datus_hive.skills as skills

    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: x\n")
    monkeypatch.setattr(skills, "_HIVE_SQL_SKILL", path)
    with pytest.raises(ValueError, match="Invalid skill frontmatter"):
        skills.get_hive_sql_generation_notes()
