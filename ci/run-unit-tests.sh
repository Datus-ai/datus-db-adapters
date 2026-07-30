#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PACKAGE_SPECS=(
  "datus-db-core:datus-db-core/tests/unit"
  "datus-sqlalchemy:datus-sqlalchemy/tests/unit"
  "datus-mysql:datus-mysql/tests/unit"
  "datus-postgresql:datus-postgresql/tests/unit"
  "datus-clickhouse:datus-clickhouse/tests/unit"
  "datus-starrocks:datus-starrocks/tests/unit"
  "datus-doris:datus-doris/tests/unit"
  "datus-trino:datus-trino/tests/unit"
  "datus-greenplum:datus-greenplum/tests/unit"
  "datus-hive:datus-hive/tests/unit"
  "datus-spark:datus-spark/tests/unit"
  "datus-redshift:datus-redshift/tests/unit"
  "datus-snowflake:datus-snowflake/tests"
  "datus-clickzetta:datus-clickzetta/tests/unit"
  "datus-maxcompute:datus-maxcompute/tests/unit"
  "datus-hologres:datus-hologres/tests/unit"
)

usage() {
  cat <<'USAGE'
Usage: ci/run-unit-tests.sh [--list] [--dry-run] [--changed base-ref] [package ...]

Runs deterministic unit tests for DB adapter workspace packages.

Options:
  --changed REF    Select impacted package targets from git diff REF...HEAD.
  --list      List configured package targets.
  --dry-run   Print selected package targets without running pytest.
  -h, --help  Show this help.
USAGE
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 127
  fi
}

list_packages() {
  local spec package test_path
  for spec in "${PACKAGE_SPECS[@]}"; do
    package="${spec%%:*}"
    test_path="${spec#*:}"
    printf '%s\t%s\n' "$package" "$test_path"
  done
}

requested_packages=()
selected_packages=()
changed_mode=0
changed_base=""
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --changed)
      if [ "$#" -lt 2 ]; then
        echo "--changed requires a base ref" >&2
        usage >&2
        exit 2
      fi
      changed_mode=1
      changed_base="$2"
      shift 2
      ;;
    --list)
      list_packages
      exit 0
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        requested_packages+=("$1")
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      requested_packages+=("$1")
      shift
      ;;
  esac
done

all_packages() {
  local spec
  for spec in "${PACKAGE_SPECS[@]}"; do
    echo "${spec%%:*}"
  done
}

packages_from_changed_files() {
  local base_ref="$1"
  local base_changed_files=""
  local staged_files=""
  local unstaged_files=""
  local untracked_files=""
  local changed_files=""

  if ! base_changed_files="$(git diff --name-only "${base_ref}...HEAD")"; then
    echo "Unable to determine changed packages from base ref '$base_ref'." >&2
    return 1
  fi
  if ! staged_files="$(git diff --name-only --cached)"; then
    echo "Unable to determine staged package changes." >&2
    return 1
  fi
  if ! unstaged_files="$(git diff --name-only)"; then
    echo "Unable to determine unstaged package changes." >&2
    return 1
  fi
  if ! untracked_files="$(git ls-files --others --exclude-standard)"; then
    echo "Unable to determine untracked package changes." >&2
    return 1
  fi

  changed_files="$(
    printf '%s\n' \
      "$base_changed_files" \
      "$staged_files" \
      "$unstaged_files" \
      "$untracked_files" |
      awk 'NF && !seen[$0]++'
  )"

  if [ -z "$changed_files" ]; then
    return 0
  fi

  if echo "$changed_files" | grep -Eq '^(pyproject\.toml|uv\.lock|ci/|\.github/workflows/|datus-db-core/|datus-sqlalchemy/)'; then
    all_packages
    return 0
  fi

  local spec package
  for spec in "${PACKAGE_SPECS[@]}"; do
    package="${spec%%:*}"
    if echo "$changed_files" | grep -Eq "^${package}/"; then
      echo "$package"
    fi
  done
}

if [ "$changed_mode" -eq 1 ]; then
  changed_packages=""
  if ! changed_packages="$(packages_from_changed_files "$changed_base")"; then
    exit 1
  fi
  while IFS= read -r package; do
    [ -n "$package" ] && selected_packages+=("$package")
  done < <(printf '%s\n' "$changed_packages" | awk '!seen[$0]++')
else
  if [ "${#requested_packages[@]}" -gt 0 ]; then
    selected_packages=("${requested_packages[@]}")
  fi
fi

if [ "${#selected_packages[@]}" -eq 0 ] && [ "$changed_mode" -eq 1 ]; then
  echo "No package changes detected; skipping unit tests."
  exit 0
fi

should_run_package() {
  local package="$1"
  if [ "${#selected_packages[@]}" -eq 0 ]; then
    return 0
  fi

  local requested
  for requested in "${selected_packages[@]}"; do
    if [ "$requested" = "$package" ]; then
      return 0
    fi
  done
  return 1
}

require_command uv

for spec in "${PACKAGE_SPECS[@]}"; do
  package="${spec%%:*}"
  test_path="${spec#*:}"

  if ! should_run_package "$package"; then
    continue
  fi

  if [ ! -e "$test_path" ]; then
    echo "Skipping $package: missing test path $test_path"
    continue
  fi

  echo ""
  echo "=== Unit tests: $package ==="
  if [ "$dry_run" -eq 1 ]; then
    echo "pytest target: $test_path"
    continue
  fi

  uv run --all-packages --with pytest --with pandas --with pyarrow pytest "$test_path" -m "not integration" --tb=short --verbose
done
