from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest
from packaging.version import Version

from ci.package_release import PackageInfo, validate_skill_resources
from ci.run_package_smoke import ordered_build_packages


def package(name: str, *dependencies: str) -> PackageInfo:
    return PackageInfo(
        name=name,
        path=Path(name),
        pyproject_path=Path(name) / "pyproject.toml",
        version=Version("0.1.0"),
        dependencies={dependency: object() for dependency in dependencies},
        import_modules=(name.replace("-", "_"),),
    )


def test_ordered_build_packages_deduplicates_shared_workspace_dependencies() -> None:
    core = package("datus-db-core")
    sqlalchemy = package("datus-sqlalchemy", "datus-db-core")
    mysql = package("datus-mysql", "datus-db-core", "datus-sqlalchemy")
    starrocks = package("datus-starrocks", "datus-db-core", "datus-mysql")
    packages = {item.name: item for item in (core, sqlalchemy, mysql, starrocks)}

    ordered = ordered_build_packages(packages, [mysql, starrocks])

    assert [item.name for item in ordered] == [
        "datus-db-core",
        "datus-sqlalchemy",
        "datus-mysql",
        "datus-starrocks",
    ]


@pytest.mark.parametrize("defect", [None, "missing_entrypoint", "missing_reference", "stale_reference"])
def test_wheel_skill_resources_are_complete_and_current(tmp_path: Path, defect: str | None) -> None:
    target = replace(package("datus-example"), path=tmp_path)
    bundle = tmp_path / "datus_example/skills/db-example-sql"
    (bundle / "references").mkdir(parents=True)
    source_files = {
        "SKILL.md": "# SQL\n[Writes](references/writes.md)\n",
        "references/writes.md": "# Writes\nUse the target dialect's write syntax.\n",
    }
    wheel = tmp_path / "datus_example.whl"
    with ZipFile(wheel, "w") as archive:
        for name, content in source_files.items():
            resource = bundle / name
            resource.write_text(content)
            if (name == "SKILL.md" and defect == "missing_entrypoint") or (
                name == "references/writes.md" and defect == "missing_reference"
            ):
                continue
            packaged = "Old reference" if name == "references/writes.md" and defect == "stale_reference" else content
            archive.writestr(resource.relative_to(tmp_path).as_posix(), packaged)

    if defect is None:
        assert validate_skill_resources(target, wheel) is None
    else:
        reason = "stale" if defect == "stale_reference" else "missing"
        with pytest.raises(ValueError, match=f"{reason} skill resource"):
            validate_skill_resources(target, wheel)
