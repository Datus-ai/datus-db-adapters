from pathlib import Path

from packaging.version import Version

from ci.package_release import PackageInfo
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
