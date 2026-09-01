from importlib.metadata import entry_points
from pathlib import Path

from datus_db_core import connector_registry
from datus_snowflake import register
from datus_snowflake.skills import get_skills_dir, get_snowflake_sql_generation_notes


def test_snowflake_sql_skill_is_packaged_and_notes_strip_frontmatter():
    skill_file = Path(get_skills_dir()) / "db-snowflake-sql" / "SKILL.md"

    assert skill_file.is_file()
    notes = get_snowflake_sql_generation_notes()
    assert notes.startswith("# Snowflake SQL")
    assert "QUALIFY" in notes
    assert "LATERAL FLATTEN" in notes
    assert "COPY INTO table" in notes
    assert "TODO" not in notes
    assert not notes.startswith("---")


def test_snowflake_registration_and_skill_entry_point():
    saved = {
        name: getattr(connector_registry, f"_{name}").copy()
        for name in ("connectors", "factories", "metadata", "capabilities", "uri_builders", "context_resolvers")
    }
    try:
        register()
        notes = connector_registry.get_sql_generation_notes("snowflake")
        candidates = entry_points().select(group="datus.skills", name="snowflake")

        assert callable(notes)
        assert notes() == get_snowflake_sql_generation_notes()
        assert len(candidates) == 1
        assert Path(next(iter(candidates)).load()()).resolve() == Path(get_skills_dir()).resolve()
    finally:
        for name, values in saved.items():
            target = getattr(connector_registry, f"_{name}")
            target.clear()
            target.update(values)
