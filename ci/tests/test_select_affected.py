import subprocess
import tomllib
from pathlib import Path

import pytest

from ci.select_affected import (
    REPO_ROOT,
    changed_files,
    load_integration_targets,
    load_packages_without_live_targets,
    load_workspace_packages,
    package_pyproject_runtime_changed,
    requirement_name,
    reverse_dependency_closure,
    select_all,
    select_impacts,
)

COMPOSE_TARGETS = {
    "postgresql",
    "mysql",
    "clickhouse",
    "starrocks",
    "doris",
    "tidb",
    "trino",
    "greenplum",
    "hive",
    "spark",
    "oracle",
    "gaussdb",
}


def select(*paths: str):
    return select_impacts(REPO_ROOT, paths)


def test_sqlalchemy_runtime_change_selects_transitive_starrocks_dependency() -> None:
    selection = select("datus-sqlalchemy/datus_sqlalchemy/connector.py")

    assert selection.compose_targets == COMPOSE_TARGETS
    assert "datus-starrocks" in selection.unit_packages


def test_mysql_runtime_change_selects_mysql_family_reverse_dependencies() -> None:
    selection = select("datus-mysql/datus_mysql/connector.py")

    assert selection.compose_targets == {"mysql", "starrocks", "doris", "tidb"}
    assert {"datus-mysql", "datus-starrocks", "datus-doris", "datus-tidb"} <= selection.unit_packages


def test_adapter_runtime_change_selects_only_that_adapter_without_dependents() -> None:
    selection = select("datus-gaussdb/datus_gaussdb/connector.py")

    assert selection.compose_targets == {"gaussdb"}
    assert selection.unit_packages == {"datus-gaussdb"}
    assert selection.smoke_packages == {"datus-gaussdb"}


def test_integration_only_change_does_not_select_reverse_dependents() -> None:
    selection = select("datus-mysql/tests/integration/test_connection.py")

    assert selection.compose_targets == {"mysql"}
    assert selection.unit_packages == {"datus-mysql"}


def test_shared_adapter_conftest_selects_its_integration_target() -> None:
    selection = select("datus-doris/tests/conftest.py")

    assert selection.compose_targets == {"doris"}


def test_unit_only_change_does_not_select_integration() -> None:
    selection = select("datus-mysql/tests/unit/test_connector_unit.py")

    assert selection.compose_targets == set()
    assert selection.unit_packages == {"datus-mysql"}


def test_documentation_change_does_not_select_tests() -> None:
    selection = select("datus-mysql/README.md", "docs/adapter-policy.md")

    assert selection.compose_targets == set()
    assert selection.unit_packages == set()
    assert selection.smoke_packages == set()


def test_root_pyproject_change_selects_all_unit_and_smoke_packages() -> None:
    packages = set(load_workspace_packages(REPO_ROOT))

    selection = select("pyproject.toml")

    assert selection.unit_packages == packages
    assert selection.smoke_packages == packages
    assert selection.compose_targets == set()
    assert selection.cloud_targets == set()


@pytest.mark.parametrize(
    "path",
    [
        "ci/integration/adapters/gaussdb.sh",
        "ci/integration/readiness/gaussdb.py",
    ],
)
def test_adapter_definition_change_selects_only_that_adapter(path: str) -> None:
    selection = select(path)

    assert selection.compose_targets == {"gaussdb"}


def test_unregistered_adapter_definition_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unregistered integration target definition"):
        select("ci/integration/adapters/unknown.sh")


@pytest.mark.parametrize(
    "path",
    [
        "ci/run-integration-tests.sh",
        "ci/integration/readiness/_common.py",
    ],
)
def test_shared_integration_infrastructure_change_selects_all_compose_targets(path: str) -> None:
    selection = select(path)

    assert selection.compose_targets == COMPOSE_TARGETS
    assert selection.cloud_targets == set()


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/test.yml",
        "ci/run-unit-tests.sh",
        "ci/select_affected.py",
    ],
)
def test_shared_unit_infrastructure_change_selects_all_unit_packages(path: str) -> None:
    selection = select(path)

    assert selection.unit_packages == set(load_workspace_packages(REPO_ROOT))


@pytest.mark.parametrize(
    "path",
    [
        "ci/package_release.py",
        "ci/run_package_smoke.py",
        "ci/select_affected.py",
    ],
)
def test_shared_smoke_infrastructure_change_selects_all_smoke_packages(path: str) -> None:
    selection = select(path)

    assert selection.smoke_packages == set(load_workspace_packages(REPO_ROOT))


@pytest.mark.parametrize(
    "path",
    [
        "ci/select_affected.py",
        "ci/integration-targets.toml",
        ".github/workflows/test.yml",
        "uv.lock",
    ],
)
def test_shared_impact_or_dependency_change_selects_all_integration_targets(path: str) -> None:
    selection = select(path)

    assert selection.compose_targets == COMPOSE_TARGETS
    assert selection.cloud_targets == {"hologres"}


@pytest.mark.parametrize(
    "path",
    [
        "ci/package_release.py",
        "ci/resolve_package_publish.py",
        "ci/tests/test_resolve_package_publish.py",
        ".github/workflows/publish-package-release.yml",
        ".github/workflows/python-format-check.yml",
    ],
)
def test_release_and_format_tooling_does_not_select_integration(path: str) -> None:
    selection = select(path)

    assert selection.compose_targets == set()


@pytest.mark.parametrize(
    ("path", "expected_targets"),
    [
        (".github/workflows/hologres-cloud-tests.yml", {"hologres"}),
        (".github/workflows/maxcompute-cloud-tests.yml", set()),
        (".github/workflows/bigquery-cloud-tests.yml", set()),
    ],
)
def test_cloud_workflow_change_selects_only_enabled_cloud_target(path: str, expected_targets: set[str]) -> None:
    selection = select(path)

    assert selection.compose_targets == set()
    assert selection.cloud_targets == expected_targets


def test_disabled_cloud_target_runs_package_checks_without_live_test() -> None:
    selection = select("datus-maxcompute/datus_maxcompute/connector.py")

    assert selection.unit_packages == {"datus-maxcompute"}
    assert selection.smoke_packages == {"datus-maxcompute"}
    assert selection.cloud_targets == set()


def test_bigquery_runtime_change_runs_package_checks_without_unconfigured_live_test() -> None:
    selection = select("datus-bigquery/datus_bigquery/connector.py")

    assert selection.unit_packages == {"datus-bigquery"}
    assert selection.smoke_packages == {"datus-bigquery"}
    assert selection.cloud_targets == set()


def test_workspace_dependency_graph_contains_transitive_starrocks_edge() -> None:
    packages = load_workspace_packages(REPO_ROOT)

    affected = reverse_dependency_closure(packages, {"datus-sqlalchemy"})

    assert "datus-mysql" in affected
    assert "datus-starrocks" in affected


def test_integration_target_manifest_covers_runner_targets() -> None:
    targets = load_integration_targets()

    assert {name for name, target in targets.items() if target.kind == "compose"} == COMPOSE_TARGETS
    assert {name for name, target in targets.items() if target.kind == "cloud"} == {
        "bigquery",
        "hologres",
        "maxcompute",
    }
    assert {name for name, target in targets.items() if target.enabled} == COMPOSE_TARGETS | {"hologres"}
    assert {name for name, target in targets.items() if not target.enabled} == {"bigquery", "maxcompute"}


def test_packages_without_live_targets_are_explicitly_registered() -> None:
    packages = load_workspace_packages(REPO_ROOT)
    target_packages = {target.package for target in load_integration_targets().values()}

    assert set(packages) - target_packages == load_packages_without_live_targets()


def test_manual_full_selection_includes_every_target() -> None:
    selection = select_all(REPO_ROOT)

    assert selection.compose_targets == COMPOSE_TARGETS
    assert selection.cloud_targets == {"hologres"}
    assert selection.unit_packages == set(load_workspace_packages(REPO_ROOT))


def test_package_version_only_change_is_not_runtime_relevant() -> None:
    before = """
[project]
name = "datus-mysql"
version = "0.1.0"
dependencies = ["datus-db-core>=0.1.0", "pymysql>=1.1.0"]
"""
    after = before.replace('version = "0.1.0"', 'version = "0.1.1"')

    assert not package_pyproject_runtime_changed(before, after)


def test_internal_dependency_lower_bound_only_change_is_not_runtime_relevant() -> None:
    before = """
[project]
name = "datus-starrocks"
dependencies = ["datus-mysql>=0.1.7"]
"""
    after = before.replace("datus-mysql>=0.1.7", "datus-mysql>=0.1.8")

    assert not package_pyproject_runtime_changed(before, after)


def test_external_dependency_change_is_runtime_relevant() -> None:
    before = """
[project]
name = "datus-mysql"
dependencies = ["pymysql>=1.1.0"]
"""
    after = before.replace("pymysql>=1.1.0", "pymysql>=1.1.1")

    assert package_pyproject_runtime_changed(before, after)


def test_version_only_pyproject_change_selects_smoke_without_integration() -> None:
    path = "datus-gaussdb/pyproject.toml"
    current = (REPO_ROOT / path).read_text(encoding="utf-8")
    current_version = tomllib.loads(current)["project"]["version"]
    before = current.replace(
        f'version = "{current_version}"',
        f'version = "{current_version}.0"',
        1,
    )
    assert before != current, "Failed to rewrite the parsed datus-gaussdb version"

    selection = select_impacts(REPO_ROOT, [path], before_text=lambda _: before)

    assert selection.compose_targets == set()
    assert selection.unit_packages == set()
    assert selection.smoke_packages == {"datus-gaussdb"}


def test_external_dependency_pyproject_change_selects_runtime_dependents() -> None:
    path = "datus-mysql/pyproject.toml"
    current = (REPO_ROOT / path).read_text(encoding="utf-8")
    current_dependencies = tomllib.loads(current)["project"]["dependencies"]
    current_requirement = next(
        (requirement for requirement in current_dependencies if requirement_name(requirement) == "pymysql"),
        None,
    )
    assert current_requirement is not None, "datus-mysql must declare its pymysql runtime dependency"
    replacement_requirement = "pymysql" if current_requirement != "pymysql" else "pymysql>=0"
    before = current.replace(current_requirement, replacement_requirement, 1)
    assert before != current, "Failed to rewrite the parsed pymysql requirement"

    selection = select_impacts(REPO_ROOT, [path], before_text=lambda _: before)

    assert selection.compose_targets == {"mysql", "starrocks", "doris", "tidb"}
    assert selection.smoke_packages == {"datus-mysql"}


def test_missing_git_base_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="git merge-base"):
        changed_files(Path(REPO_ROOT), "refs/heads/definitely-missing-test-base")


def test_changed_files_include_both_sides_of_a_rename(tmp_path: Path) -> None:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    git("init")
    git("config", "user.email", "ci-test@example.com")
    git("config", "user.name", "CI Test")
    source = tmp_path / "datus-example" / "datus_example" / "connector.py"
    source.parent.mkdir(parents=True)
    source.write_text("class Connector: pass\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "add runtime file")
    base = git("rev-parse", "HEAD").stdout.strip()

    destination = tmp_path / "datus-example" / "docs" / "connector.py"
    destination.parent.mkdir(parents=True)
    source.rename(destination)
    git("add", "-A")
    git("commit", "-m", "move runtime file to docs")

    paths, _ = changed_files(tmp_path, base)

    assert "datus-example/datus_example/connector.py" in paths
    assert "datus-example/docs/connector.py" in paths


def test_new_runtime_package_without_integration_policy_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci" / "integration-targets.toml").write_text(
        (REPO_ROOT / "ci" / "integration-targets.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["datus-new"]\n',
        encoding="utf-8",
    )
    package_dir = tmp_path / "datus-new"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text(
        '[project]\nname = "datus-new"\nversion = "0.1.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    (package_dir / "datus_new").mkdir()
    (package_dir / "datus_new" / "connector.py").write_text("class Connector: pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="no registered integration policy: datus-new"):
        select_impacts(tmp_path, ["datus-new/datus_new/connector.py"])


@pytest.mark.parametrize(
    ("runner", "selector_args"),
    [
        ("ci/run-unit-tests.sh", "--suite unit"),
        ("ci/run-integration-tests.sh", "--suite integration --kind compose"),
    ],
)
def test_shell_runners_use_the_shared_fail_closed_selector(runner: str, selector_args: str) -> None:
    source = (REPO_ROOT / runner).read_text(encoding="utf-8")

    assert f'python3 ci/select_affected.py --base "$changed_base" {selector_args}' in source
    assert "git diff --name-only" not in source
